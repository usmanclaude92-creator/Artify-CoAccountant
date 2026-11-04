# Storage Security

| Control | Implementation | Test |
|---|---|---|
| Tenant/company isolation | Forced RLS on all document tables; composite FKs; every query also filters `company_id`; cross-company ids answer 404 | `test_other_organizations_see_nothing…`, `test_company_scope_manipulated_ids…`, `test_database_isolation_for_document_tables` |
| Workspace permissions on every operation | `decide()` per node: view/upload/edit/rename/move/archive/delete; restricted mode honoured; hidden = 404 | `test_workspace_grants_govern_documents_like_folders` |
| Role gates | `documents.read/upload/manage/delete/purge`, `storage.manage` | `test_role_permissions_for_documents` |
| Permissions re-checked mid-flow | `begin_content` and `complete` re-authorise | `test_permission_changes_apply_to_upload_sessions_already_open` |
| Sessions | Revoked/expired sessions get 401 on every call; suspended organizations are read-only | `test_revoked_sessions…`, `test_suspended_organization_is_read_only` |
| Upload sessions belong to their creator | Others get 404 | `test_upload_sessions_belong_to_their_creator` |
| Path traversal | File names sanitised (no path parts, reserved characters, Windows device names); storage keys opaque and validated; local mirror paths checked against the root | `test_filenames_cannot_traverse…`, `file_names_are_windows_safe…` |
| Content validation | Magic bytes + structure; executables, macros, PDF scripts, zip bombs refused and never stored | `test_dangerous_or_damaged_content…` |
| Output hardening | Inline only for PDF/PNG/JPEG/BMP; otherwise forced download; `nosniff`; `no-store`; no redirects | `test_content_is_backend_streamed…` |
| Immutable trail | `document_events` is INSERT-only for the app role; downloads/mutations are also audited | `test_activity_trail_is_append_only…` |
| Desktop boundary | The webview never supplies a path; opaque staged ids; identifiers validated as UUIDs; query parameters whitelisted; tenant parameters never forwarded; queue sealed per user | `documents.rs` tests |
| Expired links | There are none (no pre-signed URLs); upload sessions expire after 24 h and reconciliation cleans them | `test_expired_session…`, `test_reconciliation…` |

## Malware scanning — honest status

`scanner_backend` defaults to `none`: files are **validated but not scanned**. Versions record `scan_status = not_scanned`, the desktop details panel says "Not scanned (no scanner configured)", and the Control Center and `GET /storage/overview` show `scanner.active = false`. With `scanner_backend = clamav` a `clamd` INSTREAM client is used; `infected` quarantines, a scanner error blocks the upload (`scan_unavailable`, retryable) rather than waving the file through. The client is tested against a protocol fake server only; it has **not** been run against a real ClamAV engine.
