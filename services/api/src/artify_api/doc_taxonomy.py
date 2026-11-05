"""Document types and extraction schemas (pure data, no database, no framework).

The registry is DATA: a new type or schema is one more entry here (or, later, a company-specific override loaded from
configuration). Detected types may be finer than Artify's closed catalog of document KINDS (ACCOUNTING_DATA_MAPPING §3);
`kind` maps each type onto the catalog so that workspace categories (which are mapped to kinds) can be used.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocType:
    key: str
    name: str
    kind: str | None  # Artify document kind, None when it depends on direction (sales/purchase) or is unknown
    schema: str | None  # extraction schema key
    keywords_en: tuple[str, ...] = ()
    keywords_ar: tuple[str, ...] = ()
    strong: tuple[str, ...] = ()  # phrases that are decisive when found near the top of a page


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    kind: str  # text | date | money | percent | currency | time | account
    required: bool = False


@dataclass(frozen=True)
class Schema:
    key: str
    version: int
    fields: tuple[FieldDef, ...]
    line_fields: tuple[FieldDef, ...] = ()


F = FieldDef
SCHEMAS: dict[str, Schema] = {
    "invoice": Schema(
        "invoice",
        1,
        (
            F("supplier_name", "Supplier name", "text", True),
            F("supplier_address", "Supplier address", "text"),
            F("supplier_tax_number", "Supplier tax registration number", "text"),
            F("customer_name", "Customer name", "text", True),
            F("customer_address", "Customer address", "text"),
            F("customer_tax_number", "Customer tax registration number", "text"),
            F("invoice_number", "Invoice number", "text", True),
            F("invoice_date", "Invoice date", "date", True),
            F("due_date", "Due date", "date"),
            F("po_reference", "Purchase order reference", "text"),
            F("currency", "Currency", "currency", True),
            F("description", "Description", "text"),
            F("net_amount", "Net amount", "money", True),
            F("vat_rate", "VAT / tax rate", "percent"),
            F("vat_amount", "VAT / tax amount", "money"),
            F("gross_amount", "Gross amount", "money", True),
            F("payment_terms", "Payment terms", "text"),
            F("payment_details", "Bank / payment details", "text"),
        ),
        (
            F("description", "Item description", "text"),
            F("quantity", "Quantity", "number"),
            F("unit_price", "Unit price", "money"),
            F("discount", "Discount", "money"),
            F("vat_amount", "VAT / tax", "money"),
            F("amount", "Line amount", "money"),
        ),
    ),
    "receipt": Schema(
        "receipt",
        1,
        (
            F("merchant_name", "Merchant name", "text", True),
            F("receipt_number", "Receipt number", "text"),
            F("transaction_date", "Transaction date", "date", True),
            F("transaction_time", "Transaction time", "time"),
            F("currency", "Currency", "currency", True),
            F("description", "Description", "text"),
            F("net_amount", "Net amount", "money"),
            F("vat_amount", "VAT / tax amount", "money"),
            F("total_amount", "Total amount", "money", True),
            F("payment_method", "Payment method", "text"),
            F("reference_number", "Reference number", "text"),
        ),
    ),
    "adjustment_note": Schema(
        "adjustment_note",
        1,
        (
            F("document_number", "Document number", "text", True),
            F("original_invoice_reference", "Original invoice reference", "text"),
            F("supplier_name", "Supplier name", "text"),
            F("customer_name", "Customer name", "text"),
            F("issue_date", "Issue date", "date", True),
            F("currency", "Currency", "currency", True),
            F("reason", "Reason for adjustment", "text"),
            F("net_adjustment", "Net adjustment", "money"),
            F("vat_adjustment", "VAT adjustment", "money"),
            F("gross_adjustment", "Gross adjustment", "money", True),
        ),
    ),
    "bank_statement": Schema(
        "bank_statement",
        1,
        (
            F("bank_name", "Bank name", "text"),
            F("account_holder", "Account holder", "text"),
            F("account_name", "Account name", "text"),
            F("account_number", "Account number (masked)", "account"),
            F("iban", "IBAN (masked)", "account"),
            F("currency", "Currency", "currency", True),
            F("period_start", "Statement start date", "date", True),
            F("period_end", "Statement end date", "date", True),
            F("opening_balance", "Opening balance", "money", True),
            F("closing_balance", "Closing balance", "money", True),
        ),
        (
            F("tx_date", "Transaction date", "date"),
            F("value_date", "Value date", "date"),
            F("description", "Description", "text"),
            F("reference", "Reference", "text"),
            F("debit", "Debit", "money"),
            F("credit", "Credit", "money"),
            F("balance", "Running balance", "money"),
        ),
    ),
}

# Order matters only for display; scoring decides the result.
TYPES: dict[str, DocType] = {
    t.key: t
    for t in (
        DocType(
            "sales_invoice",
            "Sales invoice",
            "sales_invoice",
            "invoice",
            ("invoice",),
            ("فاتورة",),
            ("invoice", "فاتورة"),
        ),
        DocType("purchase_invoice", "Purchase invoice", "purchase_invoice", "invoice", ("invoice",), ("فاتورة",)),
        DocType(
            "tax_invoice",
            "Tax invoice",
            None,
            "invoice",
            ("tax invoice", "vat invoice"),
            ("فاتورة ضريبية",),
            ("tax invoice", "فاتورة ضريبية"),
        ),
        DocType(
            "simplified_tax_invoice",
            "Simplified tax invoice",
            None,
            "invoice",
            ("simplified tax invoice",),
            ("فاتورة ضريبية مبسطة",),
            ("simplified tax invoice", "فاتورة ضريبية مبسطة"),
        ),
        DocType("invoice", "Invoice (direction not determined)", None, "invoice"),
        DocType(
            "receipt",
            "Receipt",
            "expense_receipt",
            "receipt",
            ("receipt", "cash sale", "thank you for shopping", "change due"),
            ("إيصال", "ايصال"),
            ("receipt", "sales receipt"),
        ),
        DocType(
            "credit_note",
            "Credit note",
            None,
            "adjustment_note",
            ("credit note", "credit memo"),
            ("إشعار دائن",),
            ("credit note", "credit memo", "إشعار دائن"),
        ),
        DocType(
            "debit_note",
            "Debit note",
            None,
            "adjustment_note",
            ("debit note", "debit memo"),
            ("إشعار مدين",),
            ("debit note", "debit memo", "إشعار مدين"),
        ),
        DocType(
            "bank_statement",
            "Bank statement",
            "bank_statement",
            "bank_statement",
            ("bank statement", "statement of account", "account statement", "opening balance", "closing balance"),
            ("كشف حساب",),
            ("bank statement", "statement of account", "account statement", "كشف حساب"),
        ),
        DocType(
            "payment_voucher",
            "Payment voucher",
            "payment_receipt",
            None,
            ("payment voucher",),
            ("سند صرف",),
            ("payment voucher", "سند صرف"),
        ),
        DocType(
            "receipt_voucher",
            "Receipt voucher",
            "payment_receipt",
            None,
            ("receipt voucher",),
            ("سند قبض",),
            ("receipt voucher", "سند قبض"),
        ),
        DocType(
            "purchase_order",
            "Purchase order",
            "reference_document",
            None,
            ("purchase order",),
            ("أمر شراء",),
            ("purchase order", "أمر شراء"),
        ),
        DocType(
            "sales_order",
            "Sales order",
            "reference_document",
            None,
            ("sales order",),
            ("أمر بيع",),
            ("sales order", "أمر بيع"),
        ),
        DocType(
            "delivery_note",
            "Delivery note",
            "inventory_document",
            None,
            ("delivery note", "goods received", "delivery order"),
            ("إشعار تسليم", "سند تسليم"),
            ("delivery note", "delivery order", "إشعار تسليم"),
        ),
        DocType(
            "payroll_document",
            "Payroll document",
            "payroll_document",
            None,
            ("payslip", "payroll", "salary slip", "net salary"),
            ("قسيمة راتب", "كشف رواتب"),
            ("payslip", "salary slip", "payroll", "قسيمة راتب"),
        ),
        DocType(
            "expense_document",
            "Expense document",
            "expense_receipt",
            None,
            ("expense claim", "expense report", "petty cash"),
            ("مطالبة مصروفات",),
            ("expense claim", "expense report"),
        ),
        DocType(
            "contract",
            "Contract",
            "reference_document",
            None,
            ("this agreement", "contract", "hereinafter", "the parties agree"),
            ("عقد", "اتفاقية"),
            ("agreement", "contract", "عقد"),
        ),
        DocType(
            "general_correspondence",
            "General correspondence",
            "reference_document",
            None,
            ("dear sir", "dear madam", "yours faithfully", "yours sincerely", "kind regards", "to whom it may concern"),
            ("السادة", "تحية طيبة"),
        ),
        DocType("unclassified", "Other / unclassified", None, None),
    )
}

#: kinds for types whose catalog kind depends on direction
DIRECTIONAL_KIND = {
    ("credit_note", "sales"): "sales_credit_note",
    ("credit_note", "purchase"): "purchase_credit_note",
    ("debit_note", "sales"): "sales_invoice",
    ("debit_note", "purchase"): "purchase_invoice",
    ("tax_invoice", "sales"): "sales_invoice",
    ("tax_invoice", "purchase"): "purchase_invoice",
    ("simplified_tax_invoice", "sales"): "sales_invoice",
    ("simplified_tax_invoice", "purchase"): "purchase_invoice",
}

CLASSIFICATION_THRESHOLD = 0.6  # below this a classification is "needs manual", never a confirmed fact
FIELD_REVIEW_THRESHOLD = 0.75  # fields below this confidence start as "needs review"

SUPPORTED_LANGUAGES = {"eng": "English", "ara": "Arabic"}
