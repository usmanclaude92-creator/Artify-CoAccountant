# Glossary — Canonical Terms

Every document in this repository uses the terms below with exactly these meanings. If a document needs a different meaning, change this file first (and the decision log, `DECISIONS.md`, if the change is architectural).

## Platform and tenancy

| Term | Definition | Database anchor |
|---|---|---|
| **Platform** | Artify itself: the operator of the SaaS. | — |
| **Platform Admin** | An Artify staff member. Authenticates in a separate auth realm. Never a member of a tenant by virtue of being a Platform Admin. | `platform_admins` |
| **Organization** | The **SaaS tenant**. Either an **accounting firm** or an **independent accountant**. Owns the subscription. | `organizations` (`type` = `accounting_firm` \| `independent_accountant`) |
| **Independent accountant** | An Organization of type `independent_accountant`. Same data model as a firm; typically a single member with a plan whose limits differ. Can be converted to `accounting_firm` by a plan/type change without data migration. | `organizations.type` |
| **Company** | A client company managed by an Organization. The **company data boundary** lives inside the tenant boundary. Belongs to exactly one Organization. | `companies.organization_id` |
| **User** | A global login identity (one row per person/email). A User has no access by itself; access comes only from memberships. | `users` |
| **Organization member** | A User with an `organization_memberships` row. Role is one of the organization roles below. | `organization_memberships` |
| **Company member** | A User with a `company_memberships` row for a Company (client-side people: company owner, employees). May or may not also be an Organization member. | `company_memberships` |
| **Accountant assignment** | Explicit link of an Organization member (accountant/staff) to a Company. Accountants and staff see only assigned Companies. | `accountant_assignments` |
| **Support access grant** | An explicit, time-limited, audited permission for a Platform Admin to access a tenant or Company. The only route by which Platform Admins can see tenant business data. | `support_access_grants` |

## Roles (system roles; seeded, versioned in code)

| Scope | Role keys | Notes |
|---|---|---|
| Platform | `platform_owner`, `platform_admin`, `platform_support`, `platform_billing`, `platform_auditor` | Separate realm. Support roles have no document access without a grant. |
| Organization | `firm_owner`, `practice_admin`, `accountant`, `staff` | In an independent-accountant Organization the single member is `firm_owner` (the UI label may read "Owner"). `firm_owner`/`practice_admin` reach all Companies of the Organization; `accountant`/`staff` reach assigned Companies only. |
| Company | `company_owner`, `company_employee` | Client-side. Confined to their own Company. |

Organizations may later define **custom roles** (a named set of permissions); permissions are a fixed catalog defined in code (`permissions`), roles are data (`roles`, `role_permissions`).

## Workspace customization

| Term | Definition |
|---|---|
| **Workspace** | A Company's own configurable document structure, categories, custom fields, filing rules, workflow and section permissions. Entirely independent per Company. |
| **Workspace configuration** | The versioned record that owns a Company's workspace. One per Company. `workspace_configurations`. |
| **Section** | A top-level node of a Workspace (e.g. "Supplier Payments"). Unlimited by design; capped only by entitlement. `workspace_sections`. |
| **Folder** | A nested node under a Section (or another Folder). `workspace_folders`. |
| **Document category** | A named classification a Document can carry (e.g. "Vendor Invoices"). Every category maps to exactly one **Document kind**. `document_categories`. |
| **Document kind** | A value from the **Artify-controlled** catalog of internal accounting concepts (`purchase_invoice`, `bank_statement`, …). Customers cannot create or rename kinds. Extraction schemas, validators and accounting rules are keyed by kind, never by customer-chosen names. `document_kinds` (reference data). |
| **Custom field** | A Company-defined, typed metadata field attached to Documents of chosen categories. `custom_field_definitions`, values in `document_field_values`. |
| **Filing rule** | A Company-defined, deterministic rule that proposes or applies a Section/Folder/Category to a Document. `filing_rules`. |
| **Intake Inbox** | The single system-reserved, non-deletable intake location every Company has. Uploads that cannot be filed land here. It is a holding area, not a business category. |
| **Change set** | An atomic batch of workspace edit operations (create/rename/move/archive…) applied as one transaction and recorded as one workspace version. |
| **Workspace template** | A reusable starting definition. Applying it **copies** it into a Company; there is no live link afterwards. `workspace_templates`, `workspace_template_versions`. |
| **Stable id / key** | Every workspace node has an immutable UUID and an immutable machine `key`. Display names are mutable. Nothing internal ever references a display name. |

## Workflow

| Term | Definition |
|---|---|
| **Workflow definition** | A Company's configurable set of stages, transitions and approval levels. Versioned. |
| **Stage** | A named step. Each stage has a customer-chosen name and an Artify-defined **stage type**: `intake`, `queued`, `processing`, `review`, `correction`, `approval`, `approved`, `exported`, `archived`. |
| **Approval** | A transition performed by an authenticated human with the required permission. System and AI actors can never perform an approval transition. |

## Accounting and documents

| Term | Definition |
|---|---|
| **Document** | A logical financial document (identity stable across versions). `documents`. |
| **Document version** | An immutable stored file. The first version is the **original** and is never modified or deleted by normal operation. `document_versions`. |
| **Extraction** | Machine-produced field candidates from a Document, with confidence and provenance. Candidates, not facts. `extracted_data`. |
| **AI suggestion** | Any AI-originated proposal (classification, account, VAT code, match). Always stored as a suggestion with status; never applied without a human action. |
| **Financial record** | Invoice, expense or bank transaction created from reviewed data. |
| **Accounting entry** | A balanced journal (header + lines) *prepared* by Artify for export. Lifecycle: `draft` → `pending_approval` → `approved` → `exported`; `rejected` returns to `draft`; corrections are made by a `reversed` counter-entry, never by editing an approved entry. |
| **Approved** (entry) | Ready for export and immutable. Artify is a preparation and document-intelligence system, **not** the general ledger of record (see D-6). The word "posted" is not used for Artify-internal state. |
| **Account role mapping** | Company mapping from an abstract role (e.g. `ap_control`, `vat_input`) to an account in the Company's chart of accounts. `account_role_mappings`. |

## Excel and reporting

| Term | Definition |
|---|---|
| **Excel template** | A Company-owned `.xlsx` layout plus mapping. Versioned and immutable per version. |
| **Template preset** | An Organization-owned (or Artify-owned) reusable template definition. Using one **copies** it into a Company. |
| **Export** | A recorded run of an Excel template version over specific approved entries; produces a file with a hash. |
| **Report configuration** | A Company-owned definition of a report (columns, grouping, filters, layout, format). |

## Subscription

| Term | Definition |
|---|---|
| **Plan** | An Artify-defined bundle of entitlements. Data, editable in Super Admin. |
| **Entitlement** | A keyed limit, quota or feature flag (`limit.*`, `quota.*`, `feature.*`). |
| **Usage counter** | A measured consumption value compared against an entitlement by the central entitlement service. |

## Sync

| Term | Definition |
|---|---|
| **Device** | A registered Desktop or Mobile installation. `devices`. |
| **Change feed** | Per-Company, monotonically sequenced log of changes clients pull to synchronize. |
| **Local mirror** | The Windows folder tree on a device derived from the Company's workspace. |
