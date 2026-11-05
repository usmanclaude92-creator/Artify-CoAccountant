# Accounting Rules (Phase 8)

Company-specific rules map documents to accounts, tax codes, narration and dimensions. **Rules are data**: a closed vocabulary, never code. There is no scripting and no expression evaluation.

## Shape (`acct_rules`)
`rule_key`, `version`, name, `kind` (`account` | `tax` | `narration` | `dimension`), `priority` (−1000…1000, higher wins), 1–10 `conditions`, an `action`, `effective_from/to`, `is_active`, `is_current`.

* Condition fields: transaction type, document type, contact id/name/tax number, description, currency, amount, document number, project, cost centre, department. Operators: `eq`, `neq`, `contains`, `starts_with`, `in`, `gte`, `lte`. All conditions must hold (AND).
* Actions: account → `{account_id, target: line|control|settlement}`; tax → `{tax_code_id}`; narration → `{template}` with only `{contact}`, `{doc_number}`, `{date}`, `{description}`, `{txn_type}`; dimension → `{name, value}`.
* Validation: unknown fields/operators/actions/placeholders are refused; referenced accounts must be active, postable and in the **same company**; tax codes must be active; contacts must exist in the company.

## Behaviour
* Applicable = active, in force on the transaction date, all conditions true.
* Highest priority wins. **Equal top priority with different results = conflict**: no value is chosen, the line is flagged `rule_conflict` (blocking) so the accountant decides. Creating a rule that may conflict returns a warning.
* An explicit account/tax code on a line always beats rules.
* Changing a rule creates a **new version**; earlier versions stay as history (`GET …/rules/{rule_key}/history`). Only the current version can be edited. Every change is audited.
* Limit: `limit.accounting_rules_per_company`; feature flag `feature.advanced_accounting`.
* Rules never cross companies. A correction is only reused when someone creates a rule for it; nothing is learned automatically and nothing is shared between tenants.
