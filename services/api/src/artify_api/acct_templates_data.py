"""Accounting templates (data only). Applying a template COPIES it into a company: afterwards the company owns an
independent chart and tax setup that later template changes can never touch (ADR on configuration independence).

* The chart is a neutral SAMPLE to speed up onboarding. It is not a statutory chart and no company is required to use it.
* The tax pack is configuration, not calculation. The Oman pack below is marked UNVERIFIED: it must be reviewed by a
  qualified tax professional, who then records the verification on each tax code, before a firm relies on it. It contains
  no exemption/zero-rating lists; deciding which treatment applies to a purchase or sale stays with the accountant."""

from __future__ import annotations

from datetime import date
from typing import Any

# code, name, account_type, parent code, postable, role
CHART_BASIC_SAMPLE: tuple[tuple[str, str, str, str | None, bool, str | None], ...] = (
    ("1000", "Assets", "asset", None, False, None),
    ("1100", "Cash and bank", "current_asset", "1000", False, None),
    ("1110", "Cash on hand", "cash", "1100", True, "cash"),
    ("1120", "Main bank account", "bank", "1100", True, "bank"),
    ("1200", "Accounts receivable", "receivable", "1000", True, "ar_control"),
    ("1300", "Input tax", "tax", "1000", True, "vat_input"),
    ("1400", "Prepayments", "current_asset", "1000", True, None),
    ("1500", "Fixed assets", "non_current_asset", "1000", True, None),
    ("2000", "Liabilities", "liability", None, False, None),
    ("2100", "Accounts payable", "payable", "2000", True, "ap_control"),
    ("2150", "Employee payables", "payable", "2000", True, "employee_payable"),
    ("2200", "Output tax", "tax", "2000", True, "vat_output"),
    ("2300", "Accruals", "current_liability", "2000", True, None),
    ("3000", "Equity", "equity", None, False, None),
    ("3100", "Owner's capital", "equity", "3000", True, None),
    ("3900", "Opening balance suspense", "equity", "3000", True, "suspense"),
    ("4000", "Revenue", "revenue", None, False, None),
    ("4100", "Sales", "revenue", "4000", True, "revenue_default"),
    ("4900", "Other income", "other_income", "4000", True, None),
    ("5000", "Cost of sales", "cost_of_sales", None, True, None),
    ("6000", "Operating expenses", "expense", None, False, None),
    ("6100", "General expenses", "expense", "6000", True, "expense_default"),
    ("6200", "Office supplies", "expense", "6000", True, None),
    ("6300", "Bank charges", "expense", "6000", True, "bank_charges"),
    ("6400", "Rent", "expense", "6000", True, None),
    ("6500", "Depreciation", "expense", "6000", True, None),
    ("6900", "Rounding differences", "other_expense", "6000", True, "rounding"),
)

# tax code, name, treatment, direction, rate (percent or None), recoverable
TAX_PACK_OM_VAT: dict[str, Any] = {
    "key": "OM_VAT",
    "jurisdiction_code": "OM",
    "name": "Oman VAT (starter configuration)",
    "verification_status": "unverified",
    "source_reference": (
        "Starter configuration based on the publicly announced Oman VAT standard rate of 5% effective 2021-04-16. "
        "NOT verified by a qualified tax professional; the applicable treatment of each supply and purchase must be "
        "confirmed before use."
    ),
    "effective_from": date(2021, 4, 16),
    "codes": (
        ("OM_STD", "Standard rated", "standard", "both", "5", True),
        ("OM_ZERO", "Zero rated", "zero_rated", "both", None, True),
        ("OM_EXEMPT", "Exempt", "exempt", "both", None, True),
        ("OM_OUT", "Out of scope", "out_of_scope", "both", None, True),
        ("OM_RC", "Reverse charge", "reverse_charge", "input", "5", True),
        ("OM_IMPORT", "Import", "import", "input", "5", True),
    ),
}
TAX_PACKS = {TAX_PACK_OM_VAT["key"]: TAX_PACK_OM_VAT}
CHARTS = {"basic_sample": CHART_BASIC_SAMPLE}
CHART_LABELS = {"basic_sample": "Basic chart of accounts (sample, not statutory)"}
