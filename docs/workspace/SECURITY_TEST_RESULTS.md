# Phase 4 Security and Data-Integrity Test Results

Every row names the executed test (files `services/api/tests/test_p4_workspace.py`, `test_p4_engine.py`, `test_database_rls.py`). Results of the run recorded in `PHASE_4_COMPLETION_REPORT.md` §15. Synthetic data only.

| Requirement | Evidence (test) | Result |
|---|---|---|
| New company has no default structure | `test_wizard_creates_…` (Phase 3 test updated to row counts) and `test_two_companies_have_different_structures…` | passed |
| 4 vs 10 sections coexist; edits in one never change the other | `test_two_companies_have_different_structures_and_never_affect_each_other` | passed |
| Cannot reference another company’s nodes/categories/fields (parent, move, category node, field scope, filing target) | `test_company_and_tenant_isolation` | passed |
| Assigned accountant cannot reach other company; other organization sees nothing | same test | passed |
| RLS: other organization’s workspace rows invisible/unchangeable; app role cannot alter revisions | `test_rls_hides_workspace_rows_across_organizations`; classification tests in `test_database_rls.py` | passed |
| Sibling uniqueness, unsafe/reserved names, bad parents | `test_validation_names_duplicates_and_parents`, `test_unsafe_node_names_are_rejected` | passed |
| Cycles / move into descendant; sections cannot move under items; destination collisions | `test_move_prevents_cycles_and_handles_subtrees` (+ database CHECK on path) | passed |
| Archive cascade, confirmation, restore order, rules switched off | `test_archive_restore_cascade_and_confirmation`, `test_restore_name_collision_is_reported` | passed |
| Delete only if never referenced; DB refuses with a RESTRICT reference | `test_delete_is_only_for_never_referenced_nodes` | passed |
| Mandatory kind mapping, validated domain/processing, file-type sanity | `test_categories_need_a_document_kind_and_validated_mapping` | passed |
| All field types, no regex/executable validation, options archived not deleted, scope, cross-company reference | `test_every_field_type_validates…`, `test_invalid_*`, `test_custom_fields_options_scope_and_archival_preserve_data` | passed |
| Naming: invalid tokens, traversal, reserved names, overwrite protection, fallback | `test_naming_*`, `test_hostile_values_cannot_escape_the_file_name`, `test_naming_rules_validate_preview_and_never_overwrite` | passed |
| Filing: condition/target validation, priority, tie-break, operators, shadow/duplicate analysis | `test_condition_validation`, `test_priority_resolution…`, `test_operators`, `test_static_analysis…`, `test_filing_rules_validate_targets_and_resolve_by_priority` | passed |
| Templates: independence, replace needs confirmation and archives, versions, copy, system read-only, atomic on failure | `test_templates_are_copied_independently`, `test_save_as_template_versions…`, `test_copy_from_another_company…`, `test_company_creation_can_start_blank…` | passed |
| Permissions: inheritance, nearest-wins, deny precedence, restricted mode, hidden names/404, read-only ceiling, administrators not lockable, who may grant | `test_grants_inherit_down…`, `test_deny_beats_allow…`, `test_restricted_mode…`, `test_read_only_members…`, `test_permissions_inheritance_deny_and_hidden_names`, `test_configuration_changes_need_company_level_configure` | passed |
| Entitlements enforced on the backend per company; existing data preserved when limits drop; template quota | `test_entitlements_are_enforced_by_the_backend_and_never_delete_data` | passed |
| History, before/after, immutable revisions, non-destructive restore, dry run, rollback on conflict | `test_revisions_audit_and_non_destructive_restore`, `test_restore_resolves_name_ownership…` | passed |
| Optimistic concurrency; inactive/archived companies read-only | `test_version_conflicts_are_detected`, `test_inactive_and_archived_companies_are_read_only` | passed |
| Distribution contract: permitted active targets only, ETag/304 | `test_upload_config_returns_only_permitted_active_targets_and_supports_etag` | passed |
| Every route declares authorization | existing route-table tests (`test_p2_authz`) | passed |
| UI accessibility (WCAG 2.0–2.2 A/AA) in light and dark on builder states | `e2e/workspace.spec.ts`, `e2e/accessibility.spec.ts` | see report |

## What was not tested

No penetration test; no mutation-testing pass on the new modules (the Phase 3 practice) — a gap; no load/performance test with large trees (`path_ids` GIN index and per-request snapshot cost are untested at scale); concurrent writers were not stress-tested (serialization relies on `SELECT … FOR UPDATE` of the configuration row); no screen-reader review.
