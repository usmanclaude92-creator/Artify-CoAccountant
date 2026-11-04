# Document Versioning

* The **document id is stable**. `documents.current_version_no` points at the newest version; `document_versions` rows are immutable.
* **New version**: register an upload with `target_document_id`. The file type must stay the same (422 otherwise), the document must be active, and identical content to the current version is refused (`identical_version`). The display name does not change.
* **Restore a version**: `POST …/versions/{n}/restore` adds a *new* version whose storage object is the same object as version *n*. History is complete, nothing is copied or lost, and restoring the current content is refused (409).
* **Download any version**: `GET …/content?version=n`.
* **Optimistic concurrency**: rename/move/archive accept `base_row_version`; a stale value answers 409 `version_conflict`.
* **Permanent deletion** removes the objects of all versions; shared objects are removed once.
* Storage accounting counts each stored object once, so a restored version costs nothing extra.

Tests: `test_versions_are_immutable_history_and_restore_adds_a_new_version`, `test_new_version_of_archived_or_foreign_document_is_refused`, `test_rename_keeps_extension_and_never_overwrites`.
