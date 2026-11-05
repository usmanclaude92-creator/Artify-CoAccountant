# Formula and Format Preservation

The writer loads the template **as a copy** (`openpyxl`, `keep_links=False`; `keep_vba` only for `.xlsm`) and edits it in place.

| Preserved | How |
|---|---|
| Formulas | Kept as text. Formula columns are **translated** down the new rows (`Translator`). `fullCalcOnLoad` is set; Artify never evaluates a formula. |
| Footer/total rows | Detected below the data region; rows are *inserted* and every reference (all sheets, defined names, ranges ending at the last template row) is shifted/extended with a tokenizer-based rewrite, so `=SUM(F5:F5)` becomes `=SUM(F5:F7)` and `Summary!B1 =Purchases!H6` becomes `=Purchases!H8`. |
| Styles | Cell styles of the template data row are copied onto new rows; row heights, column widths. |
| Merged cells, freeze panes, autofilter, print area, tables, basic conditional formats/validations | Moved or extended with the rows; a merged range that *spans* the insertion point is not extended (warning). |
| Hidden sheets | Left hidden and untouched. |

**Never overwritten:** a cell with a formula (unless explicitly allowed), a protected sheet (refused), anything outside the mapping. Unused placeholder rows are cleared (formulas left) with a warning.

**Injection guard:** any text starting with `=`, `+`, `-`, `@`, tab or CR is written as a *string* (`data_type s`, `quotePrefix`), so supplier names and narrations can never become live formulas.

**Verification (`verify_output`)** re-opens the produced file and checks that it opens, that the worksheet list matches the template, that the mapped cells of the **first and last written row** of each worksheet hold the expected values (a sample read back against the data, not every cell), and that cells above the data region were not altered. A failure aborts the export; no file is stored. It is a read-back check by the same library, not a test in Excel.

**Not preserved** (reported at analysis): charts, pivot tables, slicers, form controls, embedded objects, external links, data connections; images and threaded comments may change; Excel 2010+ extended conditional formats may be lost. Test every new template by opening an export in Excel.
