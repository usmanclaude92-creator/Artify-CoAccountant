# Financial Data Extraction (Phase 7)

Code: `doc_extract.py` (pure, no I/O), schemas in `doc_taxonomy.py`, persistence in `processing.py`.

## Schemas (versioned)
* **invoice** (sales/purchase/tax/simplified): supplier and customer name/address/tax number, invoice number and dates, PO reference, currency, description, net/VAT rate/VAT/gross amounts, payment terms and details, **line items** (description, quantity, unit price, discount, VAT, amount).
* **receipt**: merchant, receipt number, date/time, currency, net/VAT/total, payment method, reference.
* **adjustment note** (credit/debit): document number, original invoice reference, parties, date, currency, reason, net/VAT/gross adjustment.
* **bank statement**: see [BANK_STATEMENT_INTELLIGENCE.md](BANK_STATEMENT_INTELLIGENCE.md).
Other types are classified but not extracted.

## Per-field record
value (money as a Decimal string), confidence, page, bounding box, source text, method, review status, flags, and the original value after a correction. Statuses: `extracted`, `needs_review`, `confirmed`, `corrected`, `missing`, `unreadable`, `rejected`. Fields under `FIELD_REVIEW_THRESHOLD = 0.75` start as `needs_review`.

## Never invent data
A value that is not found is stored as `NULL` with status `missing` and flags (`not_found`, `ambiguous_date`, `ambiguous_amount`, `unreadable_date`…). Ambiguous numbers ("160.000" thousands vs decimals) and dates are flagged, not guessed. Money uses `Decimal`; no float exists on the path (`parse_money` rejects text and returns `Decimal`). Totals are checked (`net + VAT = gross`, line sum = net) and a mismatch is reported **with the difference**; values are never adjusted to make them agree.

## Corrections
`PATCH …/fields/{key}` with `correct | confirm | reject | missing | unreadable`. Corrections append to `field_corrections` (who, when, old, new, reason). Approved extractions are immutable (`409`); reprocessing creates a new extraction.

## Verified / not verified
Verified with synthetic English invoices, receipts and credit notes (including multi-page and wrong-OCR cases). Not verified: real customer layouts, handwritten documents, Arabic-only invoices beyond the rendered fixture, VAT regimes beyond what is printed on the document (**no tax calculation is done**).
