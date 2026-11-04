# Accounting Data Mapping

The product separates **(A) customer-configurable document organization** from **(B) the system's internal accounting model**. This document defines how A maps to B and what is guaranteed regardless of customer naming.

## 1. The two layers

| Layer | Owned by | Changes by | Examples |
|---|---|---|---|
| **A. Organization layer** | The Company | Control Center (workspace change sets) | Section "Supplier Payments", folder "2026/Q1", category "Vendor Invoices", field "PO number" |
| **B. Accounting model** | Artify (kinds, rules) + Company (chart of accounts, VAT setup, account role mappings) | Artify releases; Company configuration | document kind `purchase_invoice`, entity `invoices`, account role `ap_control`, tax code `VAT_STD` |

Customers choose names freely in A. B is never created or renamed by customers; they only *map into it*.

## 2. Mapping chain

```
Section / Folder        (A, customer names)
   │ allowed placements
   ▼
Document category       (A, customer names) ── 1 : 1 required mapping ──▶ Document kind  (B, Artify-controlled)
                                                                              │ selects
                                                                              ▼
                                   extraction schema · deterministic validators · accounting rule template
                                                                              │
                                                                              ▼
Document ─▶ extracted_data ─▶ reviewed financial record (invoices | expenses | bank_transactions)
                                                                              │ rule template + account role mappings + VAT config
                                                                              ▼
                                       accounting_entries ─▶ journal_lines ─▶ excel_exports
```

Both "Supplier Payments" (Company A) and "Vendor Invoices" (Company B) can map to document kind `purchase_invoice`; both then flow through identical extraction validation and the Company's own rules.

## 3. Document kinds (Artify-controlled catalog `document_kinds`)

Initial catalog (extensible only by Artify through migrations; each addition ships with schema, validators, rules and tests):

| Kind key | Financial concept | Creates | Posting rule template |
|---|---|---|---|
| `bank_statement` | Bank activity | `bank_transactions` (per line), reconciliation candidates | bank ↔ counter-account suggestions |
| `sales_invoice` | Receivable (accounts receivable) | `invoices` (direction = sales) | Dr AR / Cr Revenue + Cr VAT output |
| `sales_credit_note` | Receivable reduction | `invoices` (credit) | reverse of sales invoice |
| `purchase_invoice` | Payable (accounts payable) | `invoices` (direction = purchase) | Dr Expense/Asset + Dr VAT input / Cr AP |
| `purchase_credit_note` | Payable reduction | `invoices` (credit) | reverse of purchase invoice |
| `expense_receipt` | Direct expense | `expenses` | Dr Expense + Dr VAT input / Cr Cash/Bank/Payable-to-employee |
| `payment_receipt` | Settlement evidence | settlement links to invoices/bank lines | Dr AP or Cr AR vs Bank |
| `payroll_document` | Payroll evidence | none by default (reference) unless a Company-enabled rule exists | — |
| `inventory_document` | Stock movement evidence | none by default | — |
| `tax_document` | Tax authority correspondence/returns | none (reference), VAT cross-checks | — |
| `reference_document` | Contracts, correspondence, anything non-posting | none — **cannot create financial records** | — |

Note: the catalog is an initial proposal. The phase that implements each kind (Phase 7/8) may refine it through a documented migration; the **principle** (closed, Artify-owned catalog; mandatory mapping) is the architecture.

## 4. Rules that make customization safe

1. **Mandatory mapping.** `document_categories.document_kind` is `NOT NULL`. A category cannot be activated or used without it (also validated in change-set dry-run).
2. **Validation is by kind.** The accounting engine selects extraction schema, required fields, arithmetic checks and VAT checks from the document kind of the document's category. Section names never influence validation.
3. **No bypass.** A document with kind `reference_document` can never produce `invoices`/`expenses`/`bank_transactions`/`accounting_entries`: enforced by service logic and database constraints on the creating foreign keys (`source_document.kind IN (…)`). Re-categorizing a document to a different kind re-runs validation and invalidates unapproved derived records (approved records are immutable; the user must reverse them explicitly).
4. **Re-mapping a category's kind** is allowed only while the category has no financial records derived from it; otherwise the user creates a new category (history preserved).
5. **Account role mappings.** The Company's chart of accounts is its own. Rule templates refer to abstract roles (`ap_control`, `ar_control`, `vat_input`, `vat_output`, `bank`, `cash`, `expense_default`, `revenue_default`, `rounding`, …). `account_role_mappings` maps each role to a chart account (optionally per category, branch, or custom-field value such as cost center). A rule template cannot produce an entry while a required role is unmapped; the document stays in review with a clear explanation.
6. **Entries must balance.** Σ debits = Σ credits per entry in each currency, validated in the engine and by a deferred database constraint trigger before `pending_approval`; approved entries are immutable.

## 5. Traceability chain (mandatory, navigable both directions)

| Link | How it is stored |
|---|---|
| Original document | `document_versions` (version 1 = original; immutable object + SHA-256) |
| Workspace section / folder | `documents.section_id`, `folder_id`, plus `filed_path_snapshot` |
| Document category & kind | `documents.category_id`; kind resolved through the category and **snapshotted** in `documents.kind_at_filing` |
| Extracted fields | `extracted_data` rows with field path, value, confidence, provenance (page, bounding box), extractor id/version |
| Reviewed values | `document_review_events` (who changed what from which extracted value to which reviewed value) |
| Financial record | `invoices` / `expenses` / `bank_transactions` with `source_document_id` and `source_document_version_id` |
| Accounting entry | `accounting_entries.source_record_type/id`; `journal_lines` link to the record lines |
| Excel export | `excel_exports` (+ `excel_export_rows`) listing entry ids, template version id, output file hash, row ranges |

From any Excel row one can navigate back to the entry, the reviewed record, the extracted values, and the original image. The reverse navigation (from a document, which exports consumed it) is also supported.

## 6. Financial integrity rules (mapped to enforcement)

| # | Rule (brief §12) | Enforced by |
|---|---|---|
| 1 | Preserve original documents | Immutable `document_versions`; no update/delete in app role; object lock/versioning on the bucket |
| 2 | AI never silently approves/posts | ADR-009: `actor_type` checks in service + DB trigger; no AI code path to approval functions |
| 3 | AI suggestions reviewable | `ai_suggestions` with status; UI requires explicit accept; accepted suggestions are recorded as user actions |
| 4 | Deterministic validation | `accounting-engine` pure functions with unit tests; AI output is only an input to them |
| 5 | Debit = credit | Engine + deferred constraint trigger |
| 6 | Decimal arithmetic | `Decimal` everywhere, `NUMERIC` in DB (ADR-008); lint tests ban `float` in financial modules |
| 7 | Configurable jurisdiction VAT | `vat_configurations` / `tax_codes` / `tax_rates` with effective dates (see §7) |
| 8 | No hard-coded Oman VAT | Oman is a *data pack* seeded as configuration; engine contains no country constants |
| 9 | Company-specific chart of accounts | `chart_of_accounts` scoped to Company; import/mapping tools |
| 10 | Audit trails | `audit_events` + `document_review_events` + workflow history; append-only |
| 11 | Duplicate detection | Hash match + fuzzy key (vendor, invoice number, date, amount) → `duplicate_candidates` requiring human decision |
| 12 | Source-to-export traceability | §5 |
| 13 | Never invent missing data | Missing = `NULL` + flag; extraction schema distinguishes "absent" from "zero"; no defaulting of amounts/dates by AI |
| 14 | Period/currency/required-field validation | `financial_periods` (open/closed), `currencies.minor_unit`, kind-specific required fields |
| 15 | Approved records protected | Immutability triggers; reversal entries only; period locks |

## 7. VAT / tax model (jurisdiction-neutral)

- `vat_configurations`: per Company — jurisdiction code, registration status/number, filing frequency, default tax codes, rounding policy (per line vs per document), reverse-charge handling.
- `tax_codes` (per configuration): code, name, rate or rate-card, `recoverability` (fully/partially/non-recoverable), input/output direction, exemption/zero-rated flags, effective_from/effective_to. A rate change is a new dated row; historical documents keep the code/rate in force at their tax point date.
- The engine computes expected VAT with `Decimal` from base × rate under the configured rounding policy and compares with the document's stated VAT within a configured tolerance; mismatches become validation findings, never silent corrections.
- **Jurisdiction packs** (Oman initial) are versioned data bundles, reviewed by a qualified tax professional before release; packs carry a `source_reference` and `verified_on` field. Phase 8 must not claim compliance beyond the verification actually performed.

## 8. Example (mapping in practice)

Company A (4 sections) and Company B (10 sections) both receive a supplier PDF.

- A: filed in section "Payables", category "Supplier Invoices" → kind `purchase_invoice`.
- B: filed in section "Supplier Payments" → folder "Local suppliers" → category "Vendor Invoices" → kind `purchase_invoice`.

Both go through the same `purchase_invoice` extraction schema and validators. Each Company's own `account_role_mappings`, VAT configuration and Excel template determine the resulting entry and export. Renaming "Supplier Payments" to "Creditors" in B changes nothing in processing.
