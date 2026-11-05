# Field Mapping

A mapping is **declarative JSON**: no scripts, no `eval`, no free-form formulas.

```json
{ "sheets": [{ "sheet": "Purchases", "dataset": "purchase_register", "header_row": 4, "start_row": 5,
    "template_rows": 1, "formula_columns": ["J"],
    "columns": [ {"column":"A","field":"transaction_date","format":"dd/mm/yyyy"},
                 {"column":"G","field":"tax_amount","when":{"field":"tax_amount","op":"gt","value":"0"}},
                 {"column":"H","field":"gross_amount","required":true},
                 {"column":"I","kind":"static","value":"Purchases"},
                 {"column":"K","kind":"calc","op":"concat","fields":["contact_name","doc_number"],"separator":" / "} ] }],
  "fixed_cells": [ {"sheet":"Purchases","cell":"B3","kind":"field","field":"company_name"},
                   {"sheet":"Summary","cell":"B1","kind":"calc","op":"sum","of":"gross_amount","source_sheet":"Purchases"} ] }
```
* **Sources** are fields of a governed dataset (see [CUSTOM_REPORT_BUILDER.md](CUSTOM_REPORT_BUILDER.md)); header cells use `HEADER_FIELDS` (company name, period, base currency, balances…).
* **Calculations** are the whitelist `add`, `subtract`, `concat` per row and `sum`, `count`, `min`, `max` for header cells, all in `Decimal`; nothing else is accepted. **Conditions**: `present`, `absent`, `eq`, `neq`, `gt`, `lt`, `in` with an optional `else_value`.
* **Validation (`validate_mapping`)** reports: sheet not found, protected sheet, unknown dataset/field, invalid or inverted header/start rows, duplicate target, required field unmapped, invalid column/cell/format, calc type mismatch, fixed cell inside the data region, and **formula overwrite**. A target cell that currently holds a formula is refused unless the column sets `allow_formula_overwrite` explicitly. Formats beginning with `=` are refused.
* **Drafts may be incomplete.** Activation and export require zero errors; warnings are shown. Missing required values are caught again against real data at export validation.
* **Several worksheets / tables** per template are supported (one `sheets[]` entry each); filters apply per sheet only where that dataset declares them, and ignored filters are reported.
* **Immutability:** an activated mapping never changes; edit as a new mapping (a number is kept per template). Voucher numbers shown in exports are the Phase 8 transaction numbers; Phase 9 has no separate voucher sequences or presets.
