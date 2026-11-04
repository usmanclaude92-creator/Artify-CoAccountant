# Entity Catalog

> **Implementation status (end of Phase 1):** the tables listed in `PHASE_1.md` exist as migrations `0001`/`0002`. Differences from this design: `mfa_factors` has `last_used_step` (TOTP replay protection); `sessions`/`mfa_*` use two nullable subject columns (`user_id` / `platform_admin_id`) with a CHECK instead of a polymorphic id; `refresh_tokens`, `email_tokens` and `mfa_recovery_codes` are separate tables; `permissions.scope` allows `member`; `retention_policies` exists with NULL values (D-8). Company-owned tables beyond `company_memberships`, `accountant_assignments`, `invitations` and `support_access_grants` do not exist yet.

Table-by-table design. Class codes (P/R/T/C/U/D) are defined in [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md#2-ownership-classes-every-table-must-be-classified-ci-enforces). Common columns (`id`, audit columns, `row_version`, tenant columns per class) are implied and not repeated. "Key constraints" lists what is architecturally significant, not every column. This catalog is a **design baseline** that Phase 1+ migrations refine; tables are created in the phase that needs them, not all in Phase 1.

Tables beyond the brief's proposed list are marked ➕ with the reason.

---

## A. Platform and subscriptions (Phase 1 foundations, Phase 2 management)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `platform_admins` (P) | Artify staff: email, name, platform role, MFA required, status | Separate from `users`; separate credentials and sessions |
| `platform_settings` (P) | Key/value platform configuration (typed, validated), versioned | Changes platform-audited |
| `subscription_plans` (P) | Plan identity: key, name, status | key unique |
| `subscription_plan_versions` ➕ (P) | Immutable plan versions: price display data, interval, `effective_from` | Pinned by subscriptions (grandfathering) |
| `plan_entitlements` (P) | `(plan_version_id, entitlement_key, value_type, value)` | FK to `entitlement_definitions.key`; unique per version+key |
| `entitlement_definitions` ➕ (R) | Catalog of keys: type, scope, unit, enforcement mode, message key | Source of truth for valid keys |
| `subscriptions` (T) | `organization_id`, `plan_version_id`, status, period start/end, trial end, billing reference | One current subscription per Organization (partial unique index) |
| `entitlement_overrides` ➕ (T) | `(subscription_id, key, value, reason, expires_at, created_by)` | Platform-written, tenant-visible |
| `payment_records` (T) | Amounts (`NUMERIC` + currency), provider reference, status, invoice reference | Provider-agnostic (D-2); immutable |
| `usage_records` (T/C) | Append-only metering events: `metric`, `quantity`, `occurred_at`, `source_ref` | Time-partitioned; rolled up |
| `usage_counters` ➕ (T/C) | Current value per `(organization_id, company_id?, metric, period)` | Locked on update for count limits; reconcilable |
| `platform_audit_events` (P) | Actor, action, target, before/after, ip, user agent, reason | Append-only; immutable trigger |
| `support_access_grants` ➕ (T) | `platform_admin_id`, `organization_id`, `company_id?`, `access_level` (`metadata`/`read`), `reason`, `approved_by`, `starts_at`, `expires_at`, `status`, `revoked_at` | Checked by RLS and policy engine; max duration from platform setting |

## B. SaaS tenants and users (Phase 1)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `organizations` (T root) | `type` (`accounting_firm`/`independent_accountant`), legal name, display name, slug, country, default currency, `ai_processing_policy`, status (`active`/`suspended`/`closed`), `locale` | slug unique; type change audited |
| `users` (U) | Global identity: email (unique, citext), display name, locale, status, email verified | No tenant column; access only via memberships |
| `auth_credentials` ➕ (U) | Password hash (Argon2id), algorithm params, updated_at | Separate table limits blast radius |
| `mfa_factors` ➕ (U) | TOTP secret (encrypted), WebAuthn credentials, recovery codes (hashed) | |
| `sessions` ➕ (U) | Refresh-token family: hashed token, device id, created/rotated/expires, revoked_at, ip/UA | Rotation with reuse detection |
| `organization_memberships` (T) | `(organization_id, user_id, role_id, status)` | Unique (org, user); roles `firm_owner` etc. |
| `roles` (P system rows / T custom rows) | key, scope (`platform`/`organization`/`company`), is_system | Custom roles require `feature.custom_roles` |
| `permissions` (R) | Catalog of permission keys defined in code | Generated, versioned |
| `role_permissions` | `(role_id, permission_key)` | |
| `invitations` (T/C) | Invitee email, target (organization or company), role, token hash, expiry, status, inviter | Single-use, expiring |
| `devices` (U+T) | `user_id`, `organization_id`, platform, name, app version, key fingerprint, last_seen, status | Counted against `limit.devices` |

## C. Client companies (Phase 3)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `companies` (T) | Legal/display name, code, tax registration, country, base currency, fiscal year start, status | `UNIQUE (organization_id, id)` (composite-FK target); `(organization_id, code)` unique |
| `company_memberships` (C) | `(company_id, user_id, role_id, status)` | Client-side access; unique (company, user) |
| `accountant_assignments` (C) | `(company_id, user_id, assignment_type)` | `user_id` must hold an active `organization_membership` (trigger) |
| `financial_periods` (C) | Period start/end, status (`open`/`closed`/`locked`), closed_by/at | Non-overlapping (exclusion constraint) |
| `company_settings` (C) | Typed settings: naming rules, locale, rounding policy, retention overrides | Validated schema |

## D. Workspace configuration (Phase 4)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `workspace_configurations` (C) | One per Company: `version`, `applied_from_template_version_id` (provenance, nullable, **no FK dependence**), settings | `UNIQUE (company_id)` |
| `workspace_sections` (C) | `key` (immutable), `display_name` (+ localized), `sort_key`, `is_system_inbox`, `archived_at` | Unique active name per company (partial index); inbox exactly one per company; `UNIQUE (company_id, id)` |
| `workspace_folders` (C) | `section_id`, `parent_folder_id`, `key`, `display_name`, `depth`, `path_ids uuid[]`, `sort_key`, `archived_at` | Composite FK to section; depth ≤ entitlement (service + trigger); unique active sibling names |
| `document_categories` (C) | `key`, `display_name`, **`document_kind` NOT NULL → `document_kinds.key`**, default section/folder, naming rule, `archived_at` | Cannot reference unmapped kinds; kind change blocked if derived records exist |
| `custom_field_definitions` (C) | `key`, `label`, `data_type`, `required`, `validation jsonb` (schema-checked), `default_value`, `visibility`, `reference_target`, `archived_at` | Type immutable once values exist |
| `custom_field_options` ➕ (C) | Options for dropdown/multi-select/reference lists: `field_id`, `value_key`, `label`, `sort_key`, `archived_at` | Archive, don't delete |
| `custom_field_applicability` ➕ (C) | `(field_id, category_id, required)` | Which categories carry which fields |
| `document_field_values` (D) | `(document_id, field_id, option_id?)`, typed value columns, `currency_code?`, `field_definition_version` | Exactly one value column populated and matching type (trigger); unique per document+field(+option) |
| `workflow_definitions` (C) | Name, version, status, scope (categories/sections) | Versioned |
| `workflow_stages` (C) | `name`, `stage_type`, `sort_key`, SLA, `archived_at` | Definition invariants validated (≥1 intake/review/approved) |
| `workflow_transitions` (C) | `(from_stage, to_stage)`, required permission, allowed actor types, guards | Transitions to approved/approval require `actor_type='user'` in `allowed_actor_types` (CHECK) |
| `approval_rules` ➕ (C) | Approval levels: order, approver role/user, amount threshold, separation-of-duties flag | |
| `filing_rules` (C) | Priority, conditions jsonb (schema-validated), actions jsonb, mode (`propose`/`auto_file`), enabled | |
| `workspace_section_permissions` ➕ (C) | `(section_id|folder_id, principal_type, principal_id, permission)` | Grants only (no deny rows) |
| `workspace_templates` (P system / T) | Template identity: name, description, owner scope, status | System templates have `organization_id NULL` |
| `workspace_template_versions` | Immutable `definition jsonb` (schema versioned), changelog, published_at | Applying copies; no runtime joins from Companies |
| `workspace_configuration_versions` (C) | `version`, `snapshot jsonb`, `operations jsonb`, author, timestamp | Immutable; one row per change set |

## E. Documents (Phases 6–7)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `document_kinds` ➕ (R) | Catalog of Artify-controlled kinds; extraction schema ref; creates-financial-record flag | Writable only by migrations |
| `documents` (C) | `section_id`, `folder_id`, `category_id`, `kind_at_filing`, `filed_path_snapshot`, status, source (mobile/desktop/inbox), uploader, current_version_id, sha256 of original | FKs RESTRICT; `UNIQUE (company_id, original_sha256)` unless duplicate override recorded |
| `document_versions` (D) | `version_no`, storage location ref, size, content type, `sha256`, scan status | Immutable; version 1 = original |
| `document_storage_locations` (D) | Bucket, object key, encryption ref, storage class, replicated flag | Key format per ADR-011 |
| `document_processing_jobs` (D) | Stage, processor id/version, state, attempts, error, timings, units consumed | Partitioned; idempotency unique `(document_version_id, stage, processor_version)` |
| `extracted_data` (D) | Run id, field path, value (typed text/number/date), confidence, page, bbox, extractor id/version, `not_found` flag | Immutable per run; never edited |
| `document_review_events` (D) | Field path, previous value, new value, actor, reason, timestamp | Append-only |
| `ai_suggestions` ➕ (D) | Suggestion type, payload, confidence, provider/model, status (`proposed`/`accepted`/`rejected`/`superseded`), decided_by | Acceptance requires user actor |
| `duplicate_candidates` ➕ (C) | Pair of documents/records, match type (exact/fuzzy), score, status, decided_by | Human decision required |
| `workflow_instances` ➕ (D) | Document/record → current stage, definition version | |
| `workflow_history` ➕ (D) | From/to stage, `actor_type`, actor id, reason | Append-only; trigger on approved transitions |

## F. Accounting (Phase 8)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `currencies` ➕ (R) | ISO 4217 code, name, `minor_unit` | Rounding authority |
| `exchange_rates` ➕ (R/C) | Pair, rate `NUMERIC(20,10)`, date, source | Company overrides allowed |
| `chart_of_accounts` (C) | Code, name, type, parent, external code (customer's accounting software), active | Unique code per Company |
| `account_role_mappings` ➕ (C) | `(role_key, account_id, scope: category/branch/field value?)` | Required roles checked by rule templates |
| `vat_configurations` (C) | Jurisdiction, registration, filing frequency, rounding policy, reverse-charge handling, effective dates | Versioned |
| `tax_codes` ➕ (C) | Code, direction, rate(s) with effective dates, recoverability, flags | No overlapping effective ranges per code |
| `vendors` (C) / `customers` (C) | Name, tax id, aliases, default accounts/tax codes, status | Trigram indexes; merge tool audited |
| `bank_accounts` (C) | Name, bank, masked number, currency, GL account | Number stored encrypted/masked |
| `bank_transactions` (D/C) | Account, date, description, `amount`, `currency_code`, balance, source_document_id, status | Amounts `NUMERIC(20,6)`; dedupe key |
| `invoices` (D/C) + `invoice_lines` (D) | Direction (sales/purchase), party, number, dates, totals, tax breakdown, `source_document_version_id`, status | Totals checked = Σ lines (engine) |
| `expenses` (D/C) | Payee, date, category account, amounts, tax, payment method, source document | |
| `accounting_entries` (D/C) | `voucher_no`, date, period, currency, state (`draft`/`pending_approval`/`approved`/`exported`/`rejected`/`reversed`), source record ref, `approved_by/at`, `reverses_entry_id` | State transitions guarded by trigger (ADR-009); immutable once `approved` |
| `journal_lines` (D) | `entry_id`, account, `debit`, `credit`, tax code, cost center/field refs, line no | `CHECK (debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0))`; deferred balance trigger per entry+currency |
| `reconciliation_records` (C) | Bank transaction ↔ record/entry matches, status, decided_by | Suggestions are `ai_suggestions`; only decisions are stored here |

## G. Excel and reporting (Phase 9)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `excel_template_presets` (P system / T) | Reusable template definitions + file reference | Copy-on-use |
| `excel_templates` (C) | Name, purpose, status, `source_preset_id` (provenance only) | Counted against `limit.excel_templates_per_company` |
| `excel_template_versions` (D) | Version no, storage ref + SHA-256, compatibility report jsonb, published_by | Immutable |
| `excel_column_mappings` (D) | Sheet, target (column/cell/named range), source ref, formatter, row-layout mode | Schema-validated; references field ids |
| `excel_template_bindings` ➕ (C) | Template ↔ section/category/purpose | |
| `voucher_sequences` ➕ (C) | Per-Company/period numbering | Gapless policy configurable; locked allocation |
| `excel_exports` (D/C) | Template version, requested_by, parameters, output sha256, status, final flag | Immutable once final |
| `excel_export_rows` ➕ (D) | Export ↔ entry/line ↔ sheet/row | Enables traceability |
| `report_configurations` (C) | Dataset, columns, filters, grouping, layout, format, permitted roles | Schema-validated |
| `report_runs` (D/C) | Config version, params, requester, output ref, row count | |

## H. Synchronization (Phases 5–6, 12)

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `sync_devices` (C) | `(device_id, company_id)`, last cursor, last_sync_at, state | |
| `company_change_log` ➕ (C) | `seq` (per company, monotonic), entity type/id, op, version, created_at | Partitioned; `UNIQUE (company_id, seq)`; seq allocated from a per-company counter row |
| `local_file_mappings` (C+device) | `device_id`, `document_version_id`, relative path, local hash, state | |
| `sync_conflicts` (C) | Entity, both sides (jsonb), detected_at, resolution, resolved_by | |
| `sync_jobs` (C) | Server-side reconciliation/purge jobs | |
| `upload_sessions` ➕ (C) | Idempotency key, declared size/hash, target, multipart state, expires_at | |

## I. Operations

| Table (class) | Purpose / key columns | Key constraints & notes |
|---|---|---|
| `notifications` (U/C) | Recipient, type, payload, read_at, channel | |
| `daily_summaries` (C) | Date, counts by stage, exceptions | Derived; regenerable |
| `processing_jobs` (T/C) | Generic job registry for admin visibility (links to Celery ids) | |
| `audit_events` (T/C) | Actor (user/system/ai), action, entity, before/after (redacted), ip, UA, correlation id, support-grant id | Append-only; partitioned; sensitive values redacted at write |
| `outbox_events` ➕ (T/C) | Event type, payload, state, attempts | Dispatcher-owned; ADR-018 |
| `idempotency_keys` ➕ (T/U) | Key, request hash, response ref, expires_at | |

---

## Cross-cutting constraints (summary)

1. Every C/D table has `FOREIGN KEY (organization_id, company_id) → companies (organization_id, id)`.
2. Intra-Company parent references include `company_id` in the FK (parents expose `UNIQUE (company_id, id)`).
3. No `float` columns for financial values; `CHECK (currency_code ~ '^[A-Z]{3}$')` plus FK to `currencies`.
4. Immutable tables carry the immutability trigger and no `UPDATE`/`DELETE` grants.
5. Accounting integrity triggers: balanced entries, no edits after `approved`, state transitions only by permitted actor types.
6. Archive columns instead of deletes for configuration; `ON DELETE RESTRICT` for document-referenced nodes.
