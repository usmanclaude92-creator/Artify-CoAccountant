# Company Management

A **Company** is a client of the firm and the protected data boundary. Every company-owned row carries `organization_id` and `company_id` with a composite FK, RLS is forced, and the app role has no `BYPASSRLS`.

## Creating a company (4-step wizard, `/companies/new`)

1. **Basic information** — display name, code (unique per organization), legal name, registration number, company type, industry, country, city, address, contact e-mail/phone.
2. **Accounting information** — base currency, financial-year start (month/day), accounting basis (accrual/cash/unspecified), VAT registered flag and number, tax jurisdiction.
3. **Assign accountants** — optional; type (primary/secondary/reviewer) and responsibility per person; at most one primary.
4. **Review** and create.

`POST /orgs/{org}/companies` performs everything in **one transaction**: entitlement check (`limit.companies`), company row, `company_configurations` row, assignments, audit events, counter refresh. A failure anywhere rolls the whole creation back.

- Jurisdiction rules are **not** hard-coded. `tax_jurisdiction` is free configuration; the system encodes no country-specific VAT behaviour (no Oman VAT rate or rule). Later phases interpret it through configurable rule sets.
- Validation: a VAT number requires `vat_registered`; the financial-year day must exist in the month; currency and country formats are enforced by Pydantic and CHECK constraints.
- The wizard preselects country and currency from the organization defaults but never writes company values back to the organization.

## Lifecycle

| Action | From | To | Notes |
|---|---|---|---|
| deactivate | active | suspended (shown as **Inactive**) | read-only; still counts toward the company limit |
| activate | suspended | active | |
| archive | active, suspended | archived | nothing deleted; frees the subscription slot |
| restore | archived | active | needs a free slot (`limit.companies`), else 402 |

`POST /companies/{id}/status` requires `companies.archive`. There is no delete endpoint and no DELETE grant on `companies`. Editing is blocked unless the company is active (409 on archived/inactive). Status is no longer editable through `PATCH /companies/{id}` (see completion report, known issues).

## Editing

- `PATCH /companies/{id}` — profile fields (`companies.update`).
- `PATCH /companies/{id}/settings` — base currency, financial year, basis, VAT, jurisdiction (`company_settings.manage`). Each company’s settings are independent; changing one never touches another.

## Detail tabs

Overview, Accounting settings, Assigned accountants (assign, change, remove, history), Client users (company-scoped members and invitations), Configuration (Phase 4 owner record, honest “not configured”), History (company-scoped audit events).

## Visibility

Owner, administrator and practice manager reach every company. Other organization roles see only companies they are **currently** assigned to (assignment window open and active org membership). A company id outside the caller’s scope returns **404**, not 403.
