# Bank Statement Intelligence (Phase 7)

A dedicated pipeline (`parse_bank_statement`, `validate_statement`) — not the invoice extractor.

## Reads
Header: bank, holder, account name, account number and IBAN (**stored masked**: last digits only), currency, period, opening and closing balance. Rows: date, value date, description (wrapped lines are joined), reference, debit, credit, running balance, page and box per row, confidence, flags. Handles multiple pages, separate debit/credit columns or one signed column (with balance-driven direction), and rows that look alike (never merged).

## Validation (deterministic, nothing adjusted)
* opening + credits − debits = closing (`statement_reconciled` / `statement_unreconciled`, with the **difference** reported);
* each row's running balance follows from the previous row (`running_balance_break` names the suspect row) and the last row's balance equals the closing balance (`last_balance_differs`);
* rows with neither debit nor credit (`rows_without_amount`);
* missing opening/closing/running balances (`balances_incomplete`: "cannot be checked" is stated, not assumed OK).
Not checked: dates outside the period and duplicate-looking rows.
Issues are `error | warning | info`. Approving a statement that has `error` issues needs `acknowledge = true` **and** a written note.

## Review
Row actions: correct (date, description, debit, credit, balance), confirm, reject, add a missing row (marked `manual`), and edit opening/closing balances. After each change the checks re-run and the new difference is shown. Every row must be resolved before approval.

## Explicitly out of scope
No bank reconciliation, no matching to ledgers or invoices, no journal entries, no categorisation. Approval only marks the extracted data as reviewed.

## Tested
Multi-page statement, unreconciled statement (difference + suspect row), missing amount, single signed column, no balances, look-alike rows, accountant corrections and re-validation. Only with synthetic statements; no real bank's layout has been tested.
