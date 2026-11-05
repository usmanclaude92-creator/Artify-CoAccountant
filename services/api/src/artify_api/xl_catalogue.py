"""Governed datasets and the field catalogue for Excel mappings and reports (Phase 9). Pure data.

A mapping or report can only name datasets, fields and filters listed here: there is no free-form SQL and no customer-written
expression anywhere. The registry is extensible: adding a field or dataset here (with its query in `xl_data`) makes it
available to mappings and the report builder without touching the mapping engine."""

from __future__ import annotations

from dataclasses import dataclass, field

TEXT, DATE, MONEY, NUMBER = "text", "date", "money", "number"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = TEXT
    group: str = "General"


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    category: str  # financial | sales_purchase | tax | banking | management | transactions
    description: str
    fields: tuple[Field, ...]
    required: tuple[str, ...] = ()  # fields a mapping must include for the output to be meaningful
    filters: tuple[str, ...] = ("date_from", "date_to")
    custom: bool = False  # usable as a custom-report source
    groupable: tuple[str, ...] = ()
    note: str | None = None  # honest limitation shown with the report
    statutory: bool = False  # always False: no statutory compliance is claimed
    extras: dict[str, str] = field(default_factory=dict)

    def field_map(self) -> dict[str, Field]:
        return {f.key: f for f in self.fields}


def F(key: str, label: str, kind: str = TEXT, group: str = "General") -> Field:
    return Field(key, label, kind, group)


_TXN = (
    F("transaction_id", "Transaction ID", TEXT, "Accounting"),
    F("number", "Voucher / transaction number", TEXT, "Accounting"),
    F("txn_type", "Transaction type", TEXT, "Accounting"),
    F("type_label", "Transaction type (label)", TEXT, "Accounting"),
    F("transaction_date", "Transaction date", DATE, "Document"),
    F("due_date", "Due date", DATE, "Document"),
    F("doc_number", "Document number", TEXT, "Document"),
    F("reference", "Reference", TEXT, "Document"),
    F("source_document", "Source document", TEXT, "Document"),
    F("contact_name", "Customer / supplier name", TEXT, "Contact"),
    F("contact_code", "Contact code (external reference)", TEXT, "Contact"),
    F("contact_tax_number", "Tax registration number", TEXT, "Contact"),
    F("contact_address", "Address", TEXT, "Contact"),
    F("currency", "Currency", TEXT, "Financial"),
    F("exchange_rate", "Exchange rate", NUMBER, "Financial"),
    F("net_amount", "Net amount", MONEY, "Financial"),
    F("tax_amount", "VAT / tax amount", MONEY, "Financial"),
    F("gross_amount", "Gross amount", MONEY, "Financial"),
    F("base_gross_amount", "Base currency amount", MONEY, "Financial"),
    F("foreign_amount", "Foreign currency amount", MONEY, "Financial"),
    F("tax_codes", "Tax code(s)", TEXT, "Financial"),
    F("narration", "Narration", TEXT, "Accounting"),
    F("status", "Status", TEXT, "Accounting"),
    F("journal_number", "Journal number", TEXT, "Accounting"),
    F("journal_date", "Journal date", DATE, "Accounting"),
    F("project", "Project", TEXT, "Dimensions"),
    F("cost_center", "Cost center", TEXT, "Dimensions"),
    F("department", "Department", TEXT, "Dimensions"),
)
_LINE_EXTRA = (
    F("line_no", "Line number", NUMBER, "Line"),
    F("description", "Line description", TEXT, "Line"),
    F("account_code", "Account code", TEXT, "Accounting"),
    F("account_name", "Account name", TEXT, "Accounting"),
    F("tax_code", "Tax code", TEXT, "Financial"),
    F("tax_rate", "Tax rate (%)", NUMBER, "Financial"),
    F("tax_treatment", "Tax treatment", TEXT, "Financial"),
)
_JL = (
    F("journal_number", "Journal number", TEXT, "Accounting"),
    F("journal_date", "Journal date", DATE, "Accounting"),
    F("transaction_number", "Transaction number", TEXT, "Accounting"),
    F("txn_type", "Transaction type", TEXT, "Accounting"),
    F("doc_number", "Document number", TEXT, "Document"),
    F("account_code", "Account code", TEXT, "Accounting"),
    F("account_name", "Account name", TEXT, "Accounting"),
    F("account_type", "Account type", TEXT, "Accounting"),
    F("debit", "Debit", MONEY, "Financial"),
    F("credit", "Credit", MONEY, "Financial"),
    F("base_debit", "Debit (base currency)", MONEY, "Financial"),
    F("base_credit", "Credit (base currency)", MONEY, "Financial"),
    F("currency", "Currency", TEXT, "Financial"),
    F("description", "Description", TEXT, "Accounting"),
    F("reference", "Reference", TEXT, "Accounting"),
    F("narration", "Narration", TEXT, "Accounting"),
    F("contact_name", "Customer / supplier name", TEXT, "Contact"),
    F("contact_code", "Contact code (external reference)", TEXT, "Contact"),
    F("contact_tax_number", "Tax registration number", TEXT, "Contact"),
    F("tax_code", "Tax code", TEXT, "Financial"),
    F("project", "Project", TEXT, "Dimensions"),
    F("cost_center", "Cost center", TEXT, "Dimensions"),
    F("department", "Department", TEXT, "Dimensions"),
)
_TXN_FILTERS = (
    "date_from",
    "date_to",
    "txn_types",
    "contact_id",
    "account_id",
    "project",
    "cost_center",
    "department",
    "transaction_ids",
    "include_unapproved",
)
_JL_FILTERS = (
    "date_from",
    "date_to",
    "txn_types",
    "contact_id",
    "account_id",
    "project",
    "cost_center",
    "department",
    "transaction_ids",
)
_REGISTER_REQ = ("transaction_date", "number", "gross_amount")

DATASETS: dict[str, Dataset] = {
    d.key: d
    for d in (
        Dataset(
            "transactions",
            "Approved transactions",
            "transactions",
            "One row per approved accounting transaction.",
            _TXN,
            _REGISTER_REQ,
            _TXN_FILTERS,
            True,
            ("txn_type", "contact_name", "tax_codes", "project", "cost_center", "department", "month"),
        ),
        Dataset(
            "transaction_lines",
            "Approved transaction lines",
            "transactions",
            "One row per line of an approved transaction.",
            _TXN + _LINE_EXTRA,
            ("transaction_date", "number", "account_code", "net_amount"),
            _TXN_FILTERS,
            True,
            (
                "account_code",
                "account_name",
                "txn_type",
                "contact_name",
                "tax_code",
                "project",
                "cost_center",
                "department",
                "month",
            ),
        ),
        Dataset(
            "journal_lines",
            "Journal lines",
            "financial",
            "One row per line of an approved journal.",
            _JL,
            ("journal_date", "account_code", "debit", "credit"),
            _JL_FILTERS,
            True,
            (
                "account_code",
                "account_name",
                "account_type",
                "contact_name",
                "txn_type",
                "tax_code",
                "project",
                "cost_center",
                "department",
                "month",
            ),
        ),
        Dataset(
            "journal_listing",
            "Journal listing",
            "financial",
            "One row per approved journal.",
            (
                F("journal_number", "Journal number"),
                F("journal_date", "Journal date", DATE),
                F("journal_type", "Journal type"),
                F("transaction_number", "Transaction number"),
                F("narration", "Narration"),
                F("status", "Status"),
                F("total_debit", "Total debit", MONEY),
                F("total_credit", "Total credit", MONEY),
                F("currency", "Currency"),
            ),
            ("journal_number", "journal_date"),
            ("date_from", "date_to", "txn_types"),
            True,
        ),
        Dataset(
            "trial_balance",
            "Trial balance",
            "financial",
            "Account totals of approved journals (base currency).",
            (
                F("account_code", "Account code"),
                F("account_name", "Account name"),
                F("account_type", "Account type"),
                F("total_debit", "Total debits", MONEY),
                F("total_credit", "Total credits", MONEY),
                F("debit", "Debit balance", MONEY),
                F("credit", "Credit balance", MONEY),
            ),
            ("account_code", "debit", "credit"),
            ("as_of",),
        ),
        Dataset(
            "account_balances",
            "Account balances",
            "financial",
            "Balance of each account on its normal side (base currency).",
            (
                F("account_code", "Account code"),
                F("account_name", "Account name"),
                F("account_type", "Account type"),
                F("normal_balance", "Normal balance"),
                F("balance", "Balance", MONEY),
            ),
            ("account_code", "balance"),
            ("as_of",),
        ),
        Dataset(
            "general_ledger",
            "General ledger",
            "financial",
            "Every approved journal line with a running balance per account.",
            (*_JL, F("running_balance", "Running balance", MONEY, "Financial")),
            ("journal_date", "account_code", "debit", "credit"),
            ("date_from", "date_to", "account_id", "contact_id", "txn_types", "project", "cost_center", "department"),
            True,
            extras={"opening": "yes"},
        ),
        Dataset(
            "account_ledger",
            "Account ledger",
            "financial",
            "The ledger of one account with opening and closing balance.",
            (*_JL, F("running_balance", "Running balance", MONEY, "Financial")),
            ("journal_date", "debit", "credit"),
            ("date_from", "date_to", "account_id"),
            False,
            extras={"opening": "yes", "requires": "account_id"},
        ),
        Dataset(
            "profit_and_loss",
            "Income and expense summary",
            "financial",
            "Revenue and expense accounts from approved journals.",
            (
                F("section", "Section"),
                F("account_code", "Account code"),
                F("account_name", "Account name"),
                F("amount", "Amount", MONEY),
            ),
            ("section", "account_name", "amount"),
            ("date_from", "date_to"),
            note="Built from account types; not a statutory income statement. Closing entries are not implemented.",
        ),
        Dataset(
            "balance_sheet",
            "Balance sheet data",
            "financial",
            "Asset, liability and equity balances from approved journals.",
            (
                F("section", "Section"),
                F("account_code", "Account code"),
                F("account_name", "Account name"),
                F("amount", "Amount", MONEY),
            ),
            ("section", "account_name", "amount"),
            ("as_of",),
            note="Data foundation only: profit for the period is not rolled into equity (no closing entries), and no statutory format is applied.",
        ),
        Dataset(
            "sales_register",
            "Sales register",
            "sales_purchase",
            "Approved sales invoices and credit notes.",
            _TXN,
            _REGISTER_REQ,
            _TXN_FILTERS,
            True,
            ("contact_name", "tax_codes", "month"),
            extras={"types": "sales_invoice,sales_credit_note"},
        ),
        Dataset(
            "purchase_register",
            "Purchase register",
            "sales_purchase",
            "Approved purchase invoices and credit notes.",
            _TXN,
            _REGISTER_REQ,
            _TXN_FILTERS,
            True,
            ("contact_name", "tax_codes", "month"),
            extras={"types": "purchase_invoice,purchase_credit_note"},
        ),
        Dataset(
            "expense_register",
            "Expense register",
            "sales_purchase",
            "Approved expenses.",
            _TXN,
            _REGISTER_REQ,
            _TXN_FILTERS,
            True,
            ("contact_name", "txn_type", "month"),
            extras={"types": "cash_expense,bank_expense,employee_expense,general_expense"},
        ),
        Dataset(
            "receipts_register",
            "Receipts register",
            "sales_purchase",
            "Approved customer receipts and refunds.",
            _TXN,
            _REGISTER_REQ,
            _TXN_FILTERS,
            True,
            ("contact_name", "month"),
            extras={"types": "customer_receipt,customer_refund"},
        ),
        Dataset(
            "payments_register",
            "Payments register",
            "sales_purchase",
            "Approved supplier payments and refunds.",
            _TXN,
            _REGISTER_REQ,
            _TXN_FILTERS,
            True,
            ("contact_name", "month"),
            extras={"types": "supplier_payment,supplier_refund"},
        ),
        Dataset(
            "customer_ledger",
            "Customer ledger",
            "sales_purchase",
            "Receivable movements by customer, with a running balance.",
            (*_JL, F("running_balance", "Running balance", MONEY, "Financial")),
            ("journal_date", "contact_name", "debit", "credit"),
            ("date_from", "date_to", "contact_id"),
            extras={"opening": "yes"},
        ),
        Dataset(
            "supplier_ledger",
            "Supplier ledger",
            "sales_purchase",
            "Payable movements by supplier, with a running balance.",
            (*_JL, F("running_balance", "Running balance", MONEY, "Financial")),
            ("journal_date", "contact_name", "debit", "credit"),
            ("date_from", "date_to", "contact_id"),
            extras={"opening": "yes"},
        ),
        Dataset(
            "receivables_summary",
            "Receivables summary",
            "sales_purchase",
            "Amount owed by each customer on the as-of date.",
            (
                F("contact_name", "Customer"),
                F("contact_tax_number", "Tax registration number"),
                F("balance", "Balance", MONEY),
            ),
            ("contact_name", "balance"),
            ("as_of",),
            note="Balances only; no ageing or invoice-level matching exists.",
        ),
        Dataset(
            "payables_summary",
            "Payables summary",
            "sales_purchase",
            "Amount owed to each supplier on the as-of date.",
            (
                F("contact_name", "Supplier"),
                F("contact_tax_number", "Tax registration number"),
                F("balance", "Balance", MONEY),
            ),
            ("contact_name", "balance"),
            ("as_of",),
            note="Balances only; no ageing or invoice-level matching exists.",
        ),
        Dataset(
            "vat_summary",
            "Tax code summary (input / output VAT)",
            "tax",
            "Input and output tax by tax code from approved journals.",
            (
                F("tax_code", "Tax code"),
                F("tax_name", "Tax code name"),
                F("tax_treatment", "Treatment"),
                F("input_tax", "Input tax", MONEY),
                F("output_tax", "Output tax", MONEY),
                F("net_tax", "Net payable / (refundable)", MONEY),
            ),
            ("tax_code", "input_tax", "output_tax"),
            ("date_from", "date_to"),
            note="A working summary; not a VAT return and not verified against any jurisdiction's rules.",
        ),
        Dataset(
            "vat_transactions",
            "VAT transaction listing",
            "tax",
            "Tax-coded lines of approved transactions.",
            _TXN + _LINE_EXTRA + (F("direction", "Input / output", TEXT, "Financial"),),
            ("transaction_date", "tax_code", "tax_amount"),
            _TXN_FILTERS,
            True,
            ("tax_code", "contact_name", "month"),
            note="Not a VAT return.",
        ),
        Dataset(
            "bank_transactions",
            "Bank transaction listing",
            "banking",
            "Approved journal lines on bank and cash accounts with a running balance.",
            (*_JL, F("running_balance", "Running balance", MONEY, "Financial")),
            ("journal_date", "debit", "credit"),
            ("date_from", "date_to", "account_id"),
            True,
            ("account_code", "account_name", "month"),
            extras={"opening": "yes"},
            note="Money in = debit, money out = credit. This is the books' view, not a reconciliation with bank statements.",
        ),
        Dataset(
            "bank_summary",
            "Bank summary",
            "banking",
            "Movement and closing balance per bank or cash account.",
            (
                F("account_code", "Account code"),
                F("account_name", "Account name"),
                F("money_in", "Money in", MONEY),
                F("money_out", "Money out", MONEY),
                F("closing_balance", "Closing balance", MONEY),
            ),
            ("account_code", "money_in", "money_out"),
            ("date_from", "date_to", "as_of"),
        ),
        Dataset(
            "expense_analysis",
            "Expense analysis",
            "management",
            "Expense totals by account.",
            (
                F("account_code", "Account code"),
                F("account_name", "Account name"),
                F("amount", "Amount", MONEY),
                F("entries", "Entries", NUMBER),
            ),
            ("account_name", "amount"),
            ("date_from", "date_to", "project", "cost_center", "department"),
        ),
        Dataset(
            "dimension_summary",
            "Project / department / cost center summary",
            "management",
            "Expense amount by one dimension.",
            (
                F("dimension", "Dimension"),
                F("value", "Value"),
                F("amount", "Amount", MONEY),
                F("entries", "Entries", NUMBER),
            ),
            ("value", "amount"),
            ("date_from", "date_to", "group_by"),
            note="Lines without the dimension appear as '(none)'.",
        ),
    )
}
HEADER_FIELDS: tuple[Field, ...] = (
    F("company_name", "Company name"),
    F("company_code", "Company code"),
    F("base_currency", "Base currency"),
    F("period_start", "Period start", DATE),
    F("period_end", "Period end / as-of date", DATE),
    F("export_date", "Export date", DATE),
    F("report_title", "Report title"),
    F("template_name", "Template name"),
    F("record_count", "Number of records", NUMBER),
    F("total_debit", "Total debit", MONEY),
    F("total_credit", "Total credit", MONEY),
    F("total_net", "Total net", MONEY),
    F("total_tax", "Total tax", MONEY),
    F("total_gross", "Total gross", MONEY),
    F("opening_balance", "Opening balance", MONEY),
    F("closing_balance", "Closing balance", MONEY),
    F("net_profit", "Net profit / (loss)", MONEY),
    F("total_assets", "Total assets", MONEY),
    F("total_liabilities", "Total liabilities", MONEY),
    F("total_equity", "Total equity", MONEY),
)
ROW_CALCS = ("add", "subtract", "concat")  # per-row, over catalogue fields
AGGREGATES = ("sum", "count", "min", "max")  # over a dataset field, for fixed cells
CONDITION_OPS = ("present", "absent", "eq", "neq", "gt", "lt", "in")
FILTER_KEYS = (
    "date_from",
    "date_to",
    "as_of",
    "txn_types",
    "contact_id",
    "account_id",
    "project",
    "cost_center",
    "department",
    "transaction_ids",
    "include_unapproved",
    "group_by",
)
FIELD_KINDS = (TEXT, DATE, MONEY, NUMBER)


def catalogue_view() -> dict[str, object]:
    return {
        "datasets": [
            {
                "key": d.key,
                "label": d.label,
                "category": d.category,
                "description": d.description,
                "required": list(d.required),
                "filters": list(d.filters),
                "custom": d.custom,
                "groupable": list(d.groupable),
                "note": d.note,
                "statutory": d.statutory,
                "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "group": f.group} for f in d.fields],
            }
            for d in DATASETS.values()
        ],
        "header_fields": [{"key": f.key, "label": f.label, "kind": f.kind} for f in HEADER_FIELDS],
        "row_calcs": list(ROW_CALCS),
        "aggregates": list(AGGREGATES),
        "condition_ops": list(CONDITION_OPS),
    }
