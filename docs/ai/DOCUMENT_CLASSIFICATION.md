# Document Classification (Phase 7)

Code: `doc_taxonomy.py` (types, thresholds), `doc_extract.classify`, `understanding.py`.

## Types
Sales invoice, purchase invoice, tax invoice, simplified tax invoice, receipt, credit note, debit note, bank statement, payment voucher, receipt voucher, purchase order, sales order, delivery note, payroll document, expense document, contract, correspondence, other. Each type maps to an Artify-controlled **document kind**; internal logic keys off kinds through the workspace category mapping, never off display names (invariant 3). The type list is code-level and extensible (`classify(..., types=...)` accepts additional `DocType` definitions and is tested with a custom type); there is **no screen to define custom types yet**.

## Method
Deterministic keyword/pattern scoring over the recognised lines (`method = keyword_rules`), weighted by position (title lines count more). Sales vs purchase direction is decided by where the **company's own name and tax number** appear (customer vs supplier block), using the company profile; if the company cannot be located the direction is left undecided and flagged. The result stores confidence, method, evidence (the matching lines with page and box), ranked alternatives, provider and model version.

## Thresholds
`CLASSIFICATION_THRESHOLD = 0.6`: below it the classification is `needs_manual` and **no extraction is produced** until a person chooses the type. Between that and high confidence it is a suggestion the accountant confirms. Manual correction (`POST …/classification {action: correct, type_key}`) is recorded as `human_corrected` and re-running extraction reads the document as that type.

## Optional AI provider
With consent and the `feature.advanced_ai` entitlement the external provider may propose a type; its answer is validated against the known types and stored as a suggestion with `method = ai`.

## Not covered
No learned model, no training on customer documents, no classification of languages beyond English/Arabic keywords. Accuracy was tested on synthetic documents only.
