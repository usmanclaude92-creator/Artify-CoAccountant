# Workbook Analysis (advisory)

Analysis reads a workbook **without executing anything** and suggests; the person decides.

**Upload safety (`inspect_upload`)**: extension (`.xlsx`, `.xlsm` only), size cap (`excel_max_template_bytes`, 10 MB default; enforced while streaming), OLE magic (`.xls`/encrypted) refused, ZIP structure with entry-count, uncompressed-size and compression-ratio limits, `[Content_Types].xml` and `xl/workbook.xml` parsed with `defusedxml` (entity expansion refused), `vbaProject.bin` → `macro_in_xlsx` (disguised) or `macros_not_enabled` (plan), external links warned. Errors carry a stable `code` and a plain message; nothing is stored on refusal.

**Analysis (`analyze`)** per sheet: visibility, protection, merged ranges, tables, freeze panes, filters, print area, conditional-format and validation counts, formula inventory (and which columns hold formulas per row), header-row candidates, `suggested_header_row` / `suggested_start_row`, totals/footer rows, opening/closing-balance rows, empty rows/columns, blocks, defined names, and a **fidelity** list (preserved / warned / unsupported) with `risk_level` none / warned / unsupported.

The mapping builder's "Suggest from headers" only proposes columns whose heading equals a field label exactly; a person confirms every mapping. Large sheets are scanned up to a cap and marked `truncated`.
