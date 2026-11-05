# Transaction Classification (Phase 8)

## From documents
Only an extraction an accountant **approved** can create a transaction (otherwise 409). The type comes from the classification the accountant confirmed in Phase 7:

| Approved document | Transaction type |
|---|---|
| sales/purchase invoice, tax invoice with a determined direction | sales_invoice / purchase_invoice |
| credit note (direction determined) | sales_credit_note / purchase_credit_note |
| receipt | cash_expense (change it if it was paid another way) |
| expense document | general_expense |
| bank statement | one **draft per confirmed row**: bank_withdrawal (debit) or bank_deposit (credit); the counter account is chosen by a rule or the accountant |
| invoice whose direction was not determined, vouchers, orders, delivery notes, payroll, contracts, correspondence | **refused**: the accountant must confirm the type first or prepare the transaction by hand |

Direction (sale or purchase) comes from where the **company's own name and tax number** appear on the document (Phase 7). Debit notes follow the invoice direction. Amounts are taken exactly as extracted; missing amounts are flagged, and when two of net/tax/gross are present the third is derived and marked as derived.

## Customers and suppliers
Matching is a **suggestion**: by tax registration number, by exact normalised name, or by similar name (labelled "check carefully"). Nothing is linked or merged automatically; the accountant chooses (`PUT …/contact`). Invoices, credit notes, receipts/refunds and payments need a contact before approval.

## Accounts, tax codes and narration (in this order)
1. an account/tax code the accountant set on the line (always wins);
2. the highest-priority **company rule** that applies on the transaction date; two rules of the same top priority that disagree are a **conflict**: no value is chosen and the line is flagged (blocking);
3. the contact's default tax code / control account;
4. the company's role defaults (visible, labelled);
5. otherwise unresolved → blocking `missing_account_mapping`.

Tax code from a document: the printed rate must match **exactly one** active standard code in force on the document date; otherwise none is chosen and the accountant picks one. Exempt, zero-rated and out-of-scope are never guessed.

Corrections are not learned automatically. To reuse a correction, create an explicit company rule ([ACCOUNTING_RULES.md](ACCOUNTING_RULES.md)).

## Not done
No AI classification of accounts, no cross-company learning, no allocation of payments to invoices, no inventory/payroll accounting.
