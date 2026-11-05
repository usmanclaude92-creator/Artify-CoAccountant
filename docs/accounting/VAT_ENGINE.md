# VAT / Tax Engine (Phase 8)

> **This is configuration and arithmetic, not tax or legal advice.** Rates, treatments and effective dates are data that the firm must verify with a qualified tax professional. Artify has not verified any jurisdiction's rules.

## Configuration is separate from calculation
`acct_tax_codes` (per company): code, name, treatment, direction (input/output/both), input and output accounts, recoverable flag, jurisdiction code, source reference, **verification status** (`unverified` until a person records reviewer name and date; any change to the code or its rates resets it). `acct_tax_rates`: percent with `effective_from`/`effective_to`, non-overlapping. The calculation (`acct_core`) contains **no country constant**; switching jurisdiction is configuration only (tested with a second 15% code and a rate change by date).

Treatments: `standard`, `zero_rated`, `exempt`, `out_of_scope`, `reverse_charge`, `import`. Only rate-bearing treatments (standard, reverse charge, import) carry a rate; exempt/zero/out-of-scope must be 0%. `reverse_charge` and `import` need the plan flag `feature.advanced_accounting`.

## Calculation (Decimal only; `D()` refuses floats)
* tax-exclusive: net = amount (or quantity × unit price − discount); tax = round(net × rate); gross = net + tax;
* tax-inclusive: gross = amount; net = gross − tax where tax = gross − gross/(1+rate), rounded;
* rounding mode (`half_up` | `half_even`) and **scope** (`line` | `document`) are company configuration; in document scope the group's rounding difference goes to the largest line so lines still add up to the document;
* scale follows the currency's minor unit (OMR 3, USD 2); multiple rates per document are grouped.

## Validation
Tax code exists, active, **in force on the transaction date** (no rate = blocking); direction fits the transaction (an input-only code on a sale is blocking); tax account configured; printed tax versus calculation (difference reported, never altered; accepted only with acknowledgement and reason); net + tax = gross on the document; a tax-registered company with no tax code on a line gets a warning (nothing is assumed).

## Oman starter pack
`POST /accounting/templates/tax-pack {key: "OM_VAT"}` copies six codes (standard 5% from 2021-04-16, zero-rated, exempt, out of scope, reverse charge 5%, import 5%). It is marked **unverified** and states so. It lists no exempt or zero-rated supplies; deciding what applies is the accountant's job. Rates are the publicly announced standard rate and were not checked against the law by Artify.

## Reporting
`GET …/reports/tax-summary` sums input and output tax from **approved** journals by tax code. It is a working summary, **not a VAT return**; no return is generated or submitted.
