"""Workspace template definition schema and the Artify system templates (starting points, not defaults).

A template is plain data: applying it deep-copies it into a company with brand-new ids. Nothing here is consulted by
document processing; a company that never applies a template behaves identically.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from artify_api.workspace_engine import (
    FIELD_TYPES,
    KIND_BY_KEY,
    ConfigError,
    validate_conditions,
    validate_field_definition,
    validate_node_name,
)

_REF = r"^[a-z0-9][a-z0-9_-]{0,63}$"


class NodeDef(BaseModel):
    ref: str = Field(pattern=_REF)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    folders: list[NodeDef] = Field(default_factory=list)


class OptionDef(BaseModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=160)


class CategoryDef(BaseModel):
    ref: str = Field(pattern=_REF)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    document_kind: str
    accounting_domain: str | None = None
    processing_type: str | None = None
    allowed_file_types: list[str] = Field(default_factory=list, max_length=30)
    node_ref: str | None = None


class FieldDefn(BaseModel):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    data_type: str
    required: bool = False
    default_value: Any = None
    validation: dict[str, Any] = Field(default_factory=dict)
    reference_target: str | None = None
    options: list[OptionDef] = Field(default_factory=list, max_length=200)
    category_refs: list[str] = Field(default_factory=list)
    node_refs: list[str] = Field(default_factory=list)


class NamingDef(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    pattern: str = Field(min_length=1, max_length=300)
    category_ref: str | None = None


class FilingDef(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    priority: int = Field(ge=1, le=100000)
    conditions: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    target_node_ref: str
    target_category_ref: str | None = None


class TemplateDefinition(BaseModel):
    sections: list[NodeDef] = Field(default_factory=list, max_length=200)
    categories: list[CategoryDef] = Field(default_factory=list, max_length=500)
    fields: list[FieldDefn] = Field(default_factory=list, max_length=200)
    naming_rules: list[NamingDef] = Field(default_factory=list, max_length=500)
    filing_rules: list[FilingDef] = Field(default_factory=list, max_length=500)

    def node_refs(self) -> list[str]:
        out: list[str] = []

        def walk(nodes: list[NodeDef]) -> None:
            for n in nodes:
                out.append(n.ref)
                walk(n.folders)

        walk(self.sections)
        return out

    def counts(self) -> dict[str, int]:
        refs = self.node_refs()
        return {
            "sections": len(self.sections),
            "folders": len(refs) - len(self.sections),
            "categories": len(self.categories),
            "fields": len(self.fields),
            "naming_rules": len(self.naming_rules),
            "filing_rules": len(self.filing_rules),
        }

    def max_depth(self) -> int:
        def depth(nodes: list[NodeDef]) -> int:
            return 0 if not nodes else 1 + max(depth(n.folders) for n in nodes)

        return depth(self.sections)

    def check(self) -> None:
        """Cross-reference and content validation. Raises ConfigError."""
        refs = self.node_refs()

        def names(nodes: list[NodeDef]) -> None:
            seen: set[str] = set()
            for n in nodes:
                validate_node_name(n.name)
                if n.name.lower() in seen:
                    raise ConfigError(f"'{n.name}' appears twice at the same level of the template.")
                seen.add(n.name.lower())
                names(n.folders)

        names(self.sections)
        if len(set(refs)) != len(refs):
            raise ConfigError("Template node references must be unique.")
        if self.max_depth() > 12:
            raise ConfigError("Template nesting is too deep.")
        cat_refs = [c.ref for c in self.categories]
        if len(set(cat_refs)) != len(cat_refs):
            raise ConfigError("Template category references must be unique.")
        for c in self.categories:
            if c.document_kind not in KIND_BY_KEY:
                raise ConfigError(f"Category '{c.name}' maps to an unknown document kind '{c.document_kind}'.")
            if c.node_ref and c.node_ref not in refs:
                raise ConfigError(f"Category '{c.name}' points to an unknown folder.")
        field_keys = [f.ref for f in self.fields]
        if len(set(field_keys)) != len(field_keys):
            raise ConfigError("Template field keys must be unique.")
        for f in self.fields:
            if f.data_type not in FIELD_TYPES:
                raise ConfigError(f"Field '{f.label}' has an unknown type.")
            validate_field_definition(
                f.data_type, f.validation, f.default_value, [o.value for o in f.options], f.reference_target
            )
            if any(r not in cat_refs for r in f.category_refs) or any(r not in refs for r in f.node_refs):
                raise ConfigError(f"Field '{f.label}' is limited to a category or folder that is not in the template.")
        for n in self.naming_rules:
            if n.category_ref and n.category_ref not in cat_refs:
                raise ConfigError(f"Naming rule '{n.name}' points to an unknown category.")
        for r in self.filing_rules:
            if r.target_node_ref not in refs:
                raise ConfigError(f"Filing rule '{r.name}' targets a folder that is not in the template.")
            if r.target_category_ref and r.target_category_ref not in cat_refs:
                raise ConfigError(f"Filing rule '{r.name}' targets an unknown category.")
            validate_conditions(r.conditions, field_keys, [])  # category_id conditions are not allowed in templates


NodeDef.model_rebuild()


# ---------------------------------------------------------------- system templates
def _f(ref: str, name: str, *subs: NodeDef) -> NodeDef:
    return NodeDef(ref=ref, name=name, folders=list(subs))


def _cat(ref: str, name: str, kind: str, node: str, *types: str) -> CategoryDef:
    return CategoryDef(
        ref=ref, name=name, document_kind=kind, node_ref=node, allowed_file_types=list(types or ("pdf", "jpg", "png"))
    )


def _file(name: str, priority: int, kind: str, node: str, category: str | None = None) -> FilingDef:
    return FilingDef(
        name=name,
        priority=priority,
        target_node_ref=node,
        target_category_ref=category,
        conditions=[{"field": "document_kind", "operator": "equals", "value": kind}],
    )


_BASE_FIELDS = [
    FieldDefn(ref="supplier", label="Supplier", data_type="short_text", validation={"max_length": 120}),
    FieldDefn(ref="invoice_number", label="Invoice number", data_type="short_text", validation={"max_length": 60}),
    FieldDefn(ref="invoice_date", label="Invoice date", data_type="date"),
]


def _general() -> TemplateDefinition:
    return TemplateDefinition(
        sections=[
            _f(
                "finance",
                "Finance",
                _f("bank-statements", "Bank Statements"),
                _f("bank-reconciliation", "Bank Reconciliation"),
            ),
            _f(
                "sales",
                "Sales",
                _f("customer-invoices", "Customer Invoices"),
                _f("credit-notes", "Credit Notes"),
                _f("receipts", "Receipts"),
            ),
            _f(
                "purchases",
                "Purchases",
                _f("supplier-invoices", "Supplier Invoices"),
                _f("purchase-orders", "Purchase Orders"),
            ),
            _f(
                "expenses", "Expenses", _f("staff-expenses", "Staff Expenses"), _f("travel-expenses", "Travel Expenses")
            ),
            _f("payroll", "Payroll"),
            _f("taxation", "Taxation", _f("vat", "VAT")),
            _f("contracts", "Contracts"),
            _f("reports", "Management Reports"),
        ],
        categories=[
            _cat("bank-statement", "Bank Statement", "bank_statement", "bank-statements", "pdf", "csv", "xlsx"),
            _cat("customer-invoice", "Customer Invoice", "sales_invoice", "customer-invoices"),
            _cat("credit-note", "Credit Note", "sales_credit_note", "credit-notes"),
            _cat("receipt", "Payment Receipt", "payment_receipt", "receipts"),
            _cat("supplier-invoice", "Supplier Invoice", "purchase_invoice", "supplier-invoices"),
            _cat("purchase-order", "Purchase Order", "reference_document", "purchase-orders"),
            _cat("expense-receipt", "Expense Receipt", "expense_receipt", "staff-expenses"),
            _cat("payroll-doc", "Payroll Document", "payroll_document", "payroll", "pdf", "xlsx"),
            _cat("tax-doc", "Tax Document", "tax_document", "vat", "pdf"),
            _cat("contract", "Contract", "reference_document", "contracts", "pdf", "docx"),
        ],
        fields=_BASE_FIELDS,
        naming_rules=[
            NamingDef(
                name="Supplier invoices",
                pattern="{Supplier|Unknown}_{InvoiceNumber|NA}_{InvoiceDate}",
                category_ref="supplier-invoice",
            ),
            NamingDef(
                name="Bank statements", pattern="{CompanyCode}_{Period}_{DocumentType}", category_ref="bank-statement"
            ),
        ],
        filing_rules=[
            _file("Bank statements", 10, "bank_statement", "bank-statements", "bank-statement"),
            _file("Supplier invoices", 20, "purchase_invoice", "supplier-invoices", "supplier-invoice"),
            _file("Customer invoices", 30, "sales_invoice", "customer-invoices", "customer-invoice"),
            _file("Expense receipts", 40, "expense_receipt", "staff-expenses", "expense-receipt"),
        ],
    )


def _compose(
    sections: list[NodeDef], cats: list[CategoryDef], extra_fields: list[FieldDefn], files: list[FilingDef]
) -> TemplateDefinition:
    return TemplateDefinition(
        sections=sections, categories=cats, fields=[*_BASE_FIELDS, *extra_fields], filing_rules=files
    )


def _trading() -> TemplateDefinition:
    return _compose(
        [
            _f(
                "banking",
                "Banking",
                _f("bank-statements", "Bank Statements"),
                _f("bank-reconciliation", "Bank Reconciliation"),
            ),
            _f("sales", "Sales", _f("customer-invoices", "Customer Invoices"), _f("delivery-notes", "Delivery Notes")),
            _f(
                "purchases",
                "Purchases",
                _f("supplier-invoices", "Supplier Invoices"),
                _f("import-documents", "Import Documents"),
            ),
            _f("inventory", "Inventory", _f("stock-counts", "Stock Counts")),
            _f("expenses", "Expenses"),
            _f("vat", "VAT"),
            _f("reports", "Management Reports"),
        ],
        [
            _cat("bank-statement", "Bank Statement", "bank_statement", "bank-statements", "pdf", "csv"),
            _cat("customer-invoice", "Customer Invoice", "sales_invoice", "customer-invoices"),
            _cat("supplier-invoice", "Supplier Invoice", "purchase_invoice", "supplier-invoices"),
            _cat("stock-count", "Stock Count", "inventory_document", "stock-counts", "pdf", "xlsx"),
            _cat("expense-receipt", "Expense Receipt", "expense_receipt", "expenses"),
        ],
        [FieldDefn(ref="branch", label="Branch", data_type="short_text", validation={"max_length": 80})],
        [
            _file("Bank statements", 10, "bank_statement", "bank-statements", "bank-statement"),
            _file("Supplier invoices", 20, "purchase_invoice", "supplier-invoices", "supplier-invoice"),
        ],
    )


def _construction() -> TemplateDefinition:
    return _compose(
        [
            _f(
                "projects",
                "Projects",
                _f("project-contracts", "Project Contracts"),
                _f("site-expenses", "Site Expenses"),
                _f("subcontractors", "Subcontractors"),
            ),
            _f("banking", "Banking", _f("bank-statements", "Bank Statements")),
            _f(
                "purchases",
                "Purchases",
                _f("supplier-invoices", "Supplier Invoices"),
                _f("purchase-orders", "Purchase Orders"),
            ),
            _f("sales", "Sales", _f("progress-billing", "Progress Billing")),
            _f("payroll", "Payroll", _f("site-payroll", "Site Payroll")),
            _f("fixed-assets", "Fixed Assets"),
            _f("vat", "VAT"),
        ],
        [
            _cat("bank-statement", "Bank Statement", "bank_statement", "bank-statements", "pdf", "csv"),
            _cat("supplier-invoice", "Supplier Invoice", "purchase_invoice", "supplier-invoices"),
            _cat("subcontractor-invoice", "Subcontractor Invoice", "purchase_invoice", "subcontractors"),
            _cat("progress-invoice", "Progress Invoice", "sales_invoice", "progress-billing"),
            _cat("site-expense", "Site Expense", "expense_receipt", "site-expenses"),
            _cat("project-contract", "Project Contract", "reference_document", "project-contracts", "pdf", "docx"),
        ],
        [
            FieldDefn(ref="project_name", label="Project name", data_type="short_text", validation={"max_length": 120}),
            FieldDefn(
                ref="site_location", label="Site location", data_type="short_text", validation={"max_length": 120}
            ),
            FieldDefn(
                ref="contract_number", label="Contract number", data_type="short_text", validation={"max_length": 60}
            ),
            FieldDefn(ref="cost_center", label="Cost center", data_type="short_text", validation={"max_length": 60}),
        ],
        [
            _file("Bank statements", 10, "bank_statement", "bank-statements", "bank-statement"),
            _file("Supplier invoices", 20, "purchase_invoice", "supplier-invoices", "supplier-invoice"),
        ],
    )


def _professional() -> TemplateDefinition:
    return _compose(
        [
            _f(
                "clients",
                "Clients",
                _f("client-invoices", "Client Invoices"),
                _f("engagement-letters", "Engagement Letters"),
            ),
            _f("banking", "Banking", _f("bank-statements", "Bank Statements")),
            _f(
                "expenses",
                "Expenses",
                _f("supplier-invoices", "Supplier Invoices"),
                _f("staff-expenses", "Staff Expenses"),
            ),
            _f("payroll", "Payroll"),
            _f("tax", "Taxation"),
            _f("reports", "Management Reports"),
        ],
        [
            _cat("bank-statement", "Bank Statement", "bank_statement", "bank-statements", "pdf", "csv"),
            _cat("client-invoice", "Client Invoice", "sales_invoice", "client-invoices"),
            _cat("supplier-invoice", "Supplier Invoice", "purchase_invoice", "supplier-invoices"),
            _cat("expense-receipt", "Expense Receipt", "expense_receipt", "staff-expenses"),
            _cat("engagement-letter", "Engagement Letter", "reference_document", "engagement-letters", "pdf", "docx"),
        ],
        [
            FieldDefn(
                ref="client_reference", label="Client reference", data_type="short_text", validation={"max_length": 60}
            )
        ],
        [
            _file("Bank statements", 10, "bank_statement", "bank-statements", "bank-statement"),
            _file("Client invoices", 20, "sales_invoice", "client-invoices", "client-invoice"),
        ],
    )


def _manufacturing() -> TemplateDefinition:
    return _compose(
        [
            _f(
                "production",
                "Production",
                _f("bill-of-materials", "Bills of Materials"),
                _f("work-orders", "Work Orders"),
            ),
            _f(
                "purchases",
                "Purchases",
                _f("raw-materials", "Raw Material Invoices"),
                _f("supplier-invoices", "Other Supplier Invoices"),
            ),
            _f("inventory", "Inventory", _f("stock-movements", "Stock Movements")),
            _f("sales", "Sales", _f("customer-invoices", "Customer Invoices")),
            _f("banking", "Banking", _f("bank-statements", "Bank Statements")),
            _f("payroll", "Payroll"),
            _f("fixed-assets", "Fixed Assets"),
            _f("vat", "VAT"),
        ],
        [
            _cat("bank-statement", "Bank Statement", "bank_statement", "bank-statements", "pdf", "csv"),
            _cat("raw-material-invoice", "Raw Material Invoice", "purchase_invoice", "raw-materials"),
            _cat("supplier-invoice", "Supplier Invoice", "purchase_invoice", "supplier-invoices"),
            _cat("customer-invoice", "Customer Invoice", "sales_invoice", "customer-invoices"),
            _cat("stock-movement", "Stock Movement", "inventory_document", "stock-movements", "pdf", "xlsx"),
        ],
        [
            FieldDefn(
                ref="production_line", label="Production line", data_type="short_text", validation={"max_length": 60}
            )
        ],
        [
            _file("Bank statements", 10, "bank_statement", "bank-statements", "bank-statement"),
            _file("Raw materials", 20, "purchase_invoice", "raw-materials", "raw-material-invoice"),
        ],
    )


def _retail() -> TemplateDefinition:
    return _compose(
        [
            _f("stores", "Stores", _f("daily-sales", "Daily Sales Reports"), _f("cash-deposits", "Cash Deposits")),
            _f(
                "banking",
                "Banking",
                _f("bank-statements", "Bank Statements"),
                _f("card-settlements", "Card Settlements"),
            ),
            _f("purchases", "Purchases", _f("supplier-invoices", "Supplier Invoices")),
            _f("expenses", "Expenses", _f("store-expenses", "Store Expenses")),
            _f("payroll", "Payroll"),
            _f("vat", "VAT"),
            _f("reports", "Management Reports"),
        ],
        [
            _cat("bank-statement", "Bank Statement", "bank_statement", "bank-statements", "pdf", "csv"),
            _cat("card-settlement", "Card Settlement", "payment_receipt", "card-settlements"),
            _cat("supplier-invoice", "Supplier Invoice", "purchase_invoice", "supplier-invoices"),
            _cat("store-expense", "Store Expense", "expense_receipt", "store-expenses"),
            _cat("daily-sales", "Daily Sales Report", "reference_document", "daily-sales", "pdf", "xlsx"),
        ],
        [
            FieldDefn(ref="store", label="Store", data_type="short_text", validation={"max_length": 80}),
            FieldDefn(
                ref="sales_channel",
                label="Sales channel",
                data_type="dropdown",
                options=[OptionDef(value="in_store", label="In store"), OptionDef(value="online", label="Online")],
            ),
        ],
        [
            _file("Bank statements", 10, "bank_statement", "bank-statements", "bank-statement"),
            _file("Supplier invoices", 20, "purchase_invoice", "supplier-invoices", "supplier-invoice"),
        ],
    )


def _hospitality() -> TemplateDefinition:
    return _compose(
        [
            _f(
                "outlets",
                "Outlets",
                _f("daily-takings", "Daily Takings"),
                _f("tips-and-service", "Tips and Service Charge"),
            ),
            _f(
                "banking",
                "Banking",
                _f("bank-statements", "Bank Statements"),
                _f("card-settlements", "Card Settlements"),
            ),
            _f(
                "purchases",
                "Purchases",
                _f("food-and-beverage", "Food and Beverage Suppliers"),
                _f("supplier-invoices", "Other Supplier Invoices"),
            ),
            _f("payroll", "Payroll", _f("staff-rosters", "Staff Rosters")),
            _f("licences", "Licences and Permits"),
            _f("vat", "VAT"),
        ],
        [
            _cat("bank-statement", "Bank Statement", "bank_statement", "bank-statements", "pdf", "csv"),
            _cat("fnb-invoice", "Food and Beverage Invoice", "purchase_invoice", "food-and-beverage"),
            _cat("supplier-invoice", "Supplier Invoice", "purchase_invoice", "supplier-invoices"),
            _cat("daily-takings", "Daily Takings", "reference_document", "daily-takings", "pdf", "xlsx"),
            _cat("licence", "Licence or Permit", "reference_document", "licences", "pdf"),
        ],
        [FieldDefn(ref="outlet", label="Outlet", data_type="short_text", validation={"max_length": 80})],
        [
            _file("Bank statements", 10, "bank_statement", "bank-statements", "bank-statement"),
            _file("F&B suppliers", 20, "purchase_invoice", "food-and-beverage", "fnb-invoice"),
        ],
    )


# key -> (name, description, builder)
SYSTEM_TEMPLATES: dict[str, tuple[str, str, Any]] = {
    "general-business": (
        "General Business",
        "Finance, sales, purchases, expenses, payroll, taxation and reports.",
        _general,
    ),
    "trading": ("Trading", "Buying and selling goods: inventory, import documents and delivery notes.", _trading),
    "construction": ("Construction", "Project-based work with site expenses and subcontractors.", _construction),
    "professional-services": ("Professional Services", "Client billing and engagement documents.", _professional),
    "manufacturing": ("Manufacturing", "Production, raw materials and stock movements.", _manufacturing),
    "retail": ("Retail", "Stores, daily sales and card settlements.", _retail),
    "hospitality": ("Hospitality", "Outlets, food and beverage purchasing, licences.", _hospitality),
    "blank": ("Custom Blank Workspace", "An empty workspace to design from scratch.", lambda: TemplateDefinition()),
}
SYSTEM_TEMPLATE_VERSION = 1
