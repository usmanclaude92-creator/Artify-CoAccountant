# Product Architecture

Terminology follows [`GLOSSARY.md`](GLOSSARY.md). Decisions are in [`DECISIONS.md`](DECISIONS.md).

## 1. What the product is

Artify Co-Accountant is a multi-tenant accounting-operations and document-intelligence SaaS for accounting firms, independent accountants, and the client companies they serve. It automates document collection, organization, OCR/AI extraction, validation, bookkeeping preparation, VAT checking, journal-entry preparation and Excel-based export.

It adapts to each customer's existing workflow and accounting software. It does **not**:

- impose a fixed folder structure or a fixed set of document categories;
- impose one Excel format;
- replace the customer's accounting software or act as its general ledger (ADR-010, decision D-6);
- allow AI to approve or finalize financial data (ADR-009).

## 2. The ecosystem

Five deliverables, one integrated system:

| # | Deliverable | Audience | Primary job | Phase |
|---|---|---|---|---|
| 1 | **Artify Super Admin** | Artify staff | Operate the platform: tenants, plans, billing, usage, health, platform audit, support access | 2 |
| 2 | **Artify Co-Accountant Control Center** (web) | Firm owners, independent accountants, practice admins, authorized staff | Administer the practice: clients, staff, assignments, roles, workspaces, templates, VAT/tax and accounting configuration, subscription usage, audit, devices | 3–4, then 7–9 |
| 3 | **Artify Co-Accountant Desktop** (Windows; Tauri + React + TS) | Accountants; authorized client-side users | Daily operations: document processing, review, classification, entries, Excel export, closing, local folders, sync | 5–10 |
| 4 | **Artify Co-Accountant Mobile** (Flutter, Android + iOS) | Client employees and field staff; accountants | Capture and submit documents; track sync status | 11 |
| 5 | **Shared cloud backend** | all of the above | Auth, authorization, tenancy, database, storage, processing, workspace config, reporting, sync, entitlements, audit | 1 onward |

Nothing is built in a client that the backend does not already authorize and enforce. Clients are presentation and local-capability layers.

## 3. Fundamental product principles

1. **Configurable by company.** Each Company owns its workspace (sections, folders, categories, custom fields, filing rules, workflow, permissions), Excel templates, report definitions, chart of accounts and VAT setup. Changing one Company never changes another.
2. **Separation of organization from accounting meaning.** Customers organize documents with their own names; the system understands them through explicit mappings to Artify-controlled document kinds (see [`ACCOUNTING_DATA_MAPPING.md`](ACCOUNTING_DATA_MAPPING.md)).
3. **Humans approve, machines suggest.** AI and automation produce suggestions; only authenticated humans with the right permission can approve (ADR-009).
4. **Traceability end-to-end.** original document → workspace location → category → extracted fields → financial record → accounting entry → Excel export is a navigable, immutable chain.
5. **Cloud is the source of truth.** Devices cache and queue; they do not own data.
6. **Entitlements are data.** Plan limits are configured in Super Admin and enforced centrally by the backend.
7. **Isolation is layered.** Tenant and company boundaries are enforced by backend authorization, composite foreign keys and Row-Level Security.

## 4. Personas and primary journeys

| Persona | Surface | Journey (summary) |
|---|---|---|
| Artify owner/admin | Super Admin | Define plan → onboard/inspect tenant → watch usage → suspend/reactivate → handle a support request with a time-limited grant |
| Firm owner | Control Center | Subscribe → invite staff → create Company → apply workspace template → customize → assign accountants → watch processing |
| Independent accountant | Control Center + Desktop | Subscribe (single-member Organization) → create Companies → configure workspaces → process daily documents |
| Practice administrator | Control Center | Manage staff, roles, assignments, templates, devices |
| Accountant | Desktop | Open assigned Company → review extracted data → approve → prepare entries → export to Excel → daily close |
| Company employee | Mobile (Desktop optional) | Select Company → scan multi-page document → pick category from that Company's own configuration → upload → see sync status |
| Company owner | Mobile/Desktop | As employee, plus see submissions and statuses of their own Company only |

## 5. Module ownership across surfaces

The Control Center is an administration and configuration environment; it does not duplicate the Desktop bookkeeping workspace. The module-to-phase mapping is maintained in [`DEVELOPMENT_ROADMAP.md`](../development/DEVELOPMENT_ROADMAP.md#control-center-module-delivery-matrix).

## 6. Out of scope for the product (stated to prevent drift)

- Being a general ledger, trial balance or statutory-reporting system (unless D-6 changes).
- Payroll calculation, inventory valuation, payment initiation or bank connectivity. (Documents of those types can be collected and filed; calculations are not performed.)
- Tax filing submission to authorities. Artify prepares and validates VAT data; it does not submit returns.
- Any claim of legal/tax compliance beyond configured rules. Jurisdiction packs are configuration that must be verified by a qualified accountant.
