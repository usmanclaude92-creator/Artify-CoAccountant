# Chart of Accounts (Phase 8)

Every company has its **own** chart. Nothing is inherited from another company, and no universal chart is assumed.

## Model (`acct_accounts`)
code (unique **per company**, letters/digits/`.-_`, ≤ 32), name, parent, type, normal balance, optional currency restriction, `is_postable`, `is_active`, description, external software code, archived timestamp, row version, creator/editor.

Types: asset, current asset, non-current asset, liability, current liability, long-term liability, equity, revenue, other income, cost of sales, expense, other expense, tax, bank, cash, receivable, payable. Normal balance follows the type (debit for assets/expenses/bank/cash/receivable, credit for liabilities/equity/revenue/payable); a **tax** account must state its normal balance explicitly; a contradicting side is refused.

## Rules enforced by the server
1. Codes are unique within the company (the same code may exist in another company).
2. A parent must be an existing account of the **same company** and a **group** (non-postable) account.
3. Circular hierarchies are refused (walk up from the proposed parent).
4. A group account cannot be posted to; an inactive account cannot be posted to; a currency-restricted account only takes its own or the base currency (journal validation).
5. Accounts are never deleted (no DELETE grant): **archive** (inactive + `archived_at`) or restore. An account that is mapped to a role, or has active sub-accounts, cannot be archived.
6. An account that already has journal lines keeps its type and currency and stays postable.
7. Every change is audited (`accounting.account_*`).

## Role mappings (`acct_role_mappings`)
Abstract roles map to the company's own accounts: `ar_control`, `ap_control`, `employee_payable`, `vat_input`, `vat_output`, `bank`, `cash`, `bank_charges`, `expense_default`, `revenue_default`, `suspense`, `rounding`. A role needs an active postable account of a fitting type (for example `bank` needs a bank account). Defaults are always visible to the accountant: a line that used a default is labelled "Company default account"; a line without any account is a **blocking** item.

## Templates
`POST /accounting/templates/chart {key: "basic_sample"}` copies a neutral sample chart (not statutory) into an **empty** chart and maps its roles. The copy is independent; there is no link back to the template.
