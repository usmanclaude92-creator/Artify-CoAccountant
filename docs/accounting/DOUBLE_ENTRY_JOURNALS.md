# Double-Entry Journals (Phase 8)

## Model
One journal per transaction (`UNIQUE (transaction_id)`, so duplicate posting is impossible). `acct_journals`: number (assigned at approval, gap-free per company), type, date, period, narration, status (`draft → approved → posted? → reversed`), transaction currency, base currency, exchange rate/source/date, totals, created/approved by, reversal link. `acct_journal_lines`: account, debit, credit (transaction currency), base debit/credit, description, reference, tax code, contact, dimensions (project, cost centre, department), origin (`generated`, `manual`, `rounding`).

## Generation (pure, deterministic: `acct_core.build_journal`)
Amounts are positive; direction comes from the type. Examples (accounts are the company's own, resolved as in [TRANSACTION_CLASSIFICATION.md](TRANSACTION_CLASSIFICATION.md)):

| Type | Debit | Credit |
|---|---|---|
| purchase invoice | expense (net, plus non-recoverable tax), input tax | payable (gross) |
| sales invoice | receivable (gross) | revenue (net), output tax |
| credit notes | mirror of the invoice | mirror of the invoice |
| customer receipt / supplier refund | bank or cash | receivable / payable |
| supplier payment / customer refund | payable / receivable | bank or cash |
| cash / bank / employee / general expense | expense, input tax | cash, bank, employee payable or payable |
| bank deposit / withdrawal / charges | see counter account | bank |
| bank transfer | destination account | source bank |
| journal entry, opening, adjustment, accrual, prepayment, depreciation | typed by the accountant | typed by the accountant |
| reverse-charge purchase | expense, input tax | **output tax**, payable for the net only |

## Validation (server only; the frontend cannot override it)
At least two lines with amounts; no negative amounts; never both a debit and a credit on one line; every line has an account that exists in **this company**, is active and postable and (if restricted) fits the currency; **Σ debit = Σ credit** in the transaction currency **and** in the base currency; required dimensions present; the date falls in an existing, **open** period; the exchange rate exists for foreign currency; duplicates are reviewed. Blocking issues stop submission and approval. Soft differences (a printed total that differs from the calculation) can be approved only with an explicit acknowledgement and a written reason, which is stored.

## Editing
Drafts only. The accountant may change lines (the journal is regenerated), or edit the **journal lines directly** (add/remove/change): the journal then stays `manual` until "regenerate". The backend validates both.

## Database guarantees (migration 0009 triggers)
* a journal can only become `approved` if it has ≥ 2 lines and balances in both currencies;
* approved/posted/reversed journals and their lines cannot be updated or deleted; only status progress (`approved → posted → reversed`) is allowed;
* a transaction can only become `approved` with an approved journal and (per policy) an approver different from the preparer;
* transactions are never deleted;
* `acct_approvals` is append-only and `approve`/`review_approve`/`reverse` rows must have `actor_type = 'user'`.

## Reversal and amendment
`POST …/reverse {reversal_date, reason}` (permission `accounting.reverse`): creates a mirror journal dated in an **open** period, approved under the reverser's name; the original becomes `reversed`; both journals stay in the ledger so balances net to zero and history is reproducible. `…/amend` reverses and opens a corrected draft copy. A reversed transaction cannot be reversed again.
