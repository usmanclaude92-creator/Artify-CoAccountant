# Duplicate Detection

* **Method**: exact SHA-256 of the stored bytes (`documents.content_sha256`), company-scoped. No similarity matching.
* **Where it runs**: at registration when the client announces a checksum (the desktop always does), and again after the bytes arrive (authoritative). `POST …/duplicates/check` lets the UI look before queuing.
* **Policy** per upload: `block` (default; 409 `duplicate_document` with the existing documents) or `allow_copy`.
* **No leakage**: matches are limited to the same company (the query filters on `company_id` and RLS applies) **and** to documents whose folder the caller can view. Identical files in another company, or in a folder hidden from the caller, are never reported. Deleted and purged documents do not count.
* **New versions**: identical content to the current version is refused as `identical_version`.

Tests: `test_duplicates_are_company_scoped_and_policy_driven`, `test_workspace_grants_govern_documents_like_folders` (hidden duplicates).
