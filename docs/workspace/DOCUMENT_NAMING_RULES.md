# Document Naming Rules

Configuration, validation and preview. Since Phase 6 the same engine renders the *suggested* document name at import ([`../documents/DOCUMENT_NAMING_AND_FILING.md`](../documents/DOCUMENT_NAMING_AND_FILING.md)); the stored name is whatever the user accepted.

## Pattern syntax

Text with `{Token}` placeholders; `{Token|fallback}` supplies a fallback for a missing value. A pattern must contain at least one token. Tokens are case-insensitive and ignore `_`/`-`.

Built-in tokens: `Company`, `CompanyCode`, `DocumentType`, `DocumentNumber`, `DocumentDate`, `Date`, `Year`, `Month`, `Period` (YYYY-MM), `FinancialYear` (from the company’s financial-year start month, e.g. `2025-2026`), `Supplier`, `Customer`, `Project`, `Branch`, `Category`. Any **active custom field key** of the same company is also a token (`{cost_center}`, `{InvoiceNumber}` ↔ `invoice_number`); an ambiguous match is rejected. A custom field of another company is not available.

## Safety

- Fixed text may not contain `/ \ : * ? " < > |`, control characters or `..` (path traversal is impossible by construction).
- Every substituted value is sanitized (NFKC, separators replaced, whitespace → `_`, leading/trailing dots stripped, length-limited); the final name is capped (150 chars), Windows reserved names (`CON`, `NUL`, `COM1`…) get a suffix, and the extension is restricted to `[a-z0-9]{≤10}`.
- Missing values use the fallback or `NA` and are returned as **warnings** (never silently).
- Overwrite protection: `ensure_unique_name` appends `_2`, `_3`… (case-insensitive) given the existing names.
- No rule applies → deterministic fallback `<kind>_<date|undated>_<id suffix>[.ext]`.
- The **original file name is stored separately** (the preview echoes it unchanged).

## API

`POST …/naming-rules/validate`, `POST …/naming-rules/preview` (pattern or category rule, sample values, original file name, existing names), CRUD + archive/restore. One live rule per category and one company default (database partial unique index).
