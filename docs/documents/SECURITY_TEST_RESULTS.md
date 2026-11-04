# Phase 6 Security Test Results

Results are filled in from the final runs recorded in the [completion report](../development/PHASE_6_COMPLETION_REPORT.md). Only tests that were executed are listed.

| Area | Test(s) | Result |
|---|---|---|
| Cross-organization access (list, read, content, counts, events, mutations, upload) | `test_other_organizations_see_nothing_and_learn_nothing` | passed |
| Cross-company access and manipulated ids (random, malformed, injection strings, other company's node/category/document) | `test_company_scope_manipulated_ids_and_cross_company_references` | passed |
| Upload sessions of other users | `test_upload_sessions_belong_to_their_creator` | passed |
| Role matrix (read-only, document operator, accountant, practice manager) | `test_role_permissions_for_documents` | passed |
| Workspace allow/deny/restricted mode govern documents | `test_workspace_grants_govern_documents_like_folders` | passed |
| Permission changes during an open upload | `test_permission_changes_apply_to_upload_sessions_already_open` | passed |
| Revoked session / archived company / suspended organization | `test_revoked_sessions_and_inactive_companies…`, `test_suspended_organization_is_read_only` | passed |
| Path traversal and unsafe names | `test_filenames_cannot_traverse_or_collide_with_system_names` | passed |
| Dangerous / damaged content (14 cases + zip bomb) | `test_dangerous_or_damaged_content_is_rejected_and_never_stored`, `test_zip_bomb_is_refused` | passed |
| Row-level security and FK/CHECK guards | `test_database_isolation_for_document_tables`, `test_every_table_is_classified_and_protected` | passed |
| Append-only trail | `test_activity_trail_is_append_only_even_for_the_application_role` | passed |
| Entitlement enforcement without data loss | storage / per-month / file-size / suspended tests | passed |
| Scanner behaviour and clamd protocol | `test_scanner_verdicts_decide_what_is_stored`, `test_clamd_client_speaks_instream_against_a_protocol_fake` | passed (fake server only) |
| Desktop: ids not paths, whitelisted queries, sealed queue, user-scoped queue | `core/tests/documents.rs` | passed |
| Desktop ↔ real API | `live_backend.rs::documents_are_imported_stored_verified_listed_and_saved_locally…` | passed |

## Known gaps (not tested or not testable here)

Real ClamAV; real S3/MinIO; Windows-only behaviour (WebView2 PDF viewer, Explorer open, native drag-and-drop, installer); load/performance at scale; a concurrent-upload race harness beyond the serialised counter lock.
