"""Default daily checklist templates (Phase 10).

These are starting points that a company COPIES. After the copy nothing links the company's list back to the template, so
editing, renaming, reordering or archiving a company's list never affects another company, and changing a template here never
reaches a company that already copied it. Items name their rule by key; the rule is evaluated from data, never from the
title, so renaming an item cannot change what it checks."""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "daily_bookkeeping": {
        "name": "Daily bookkeeping",
        "description": "What is normally checked before a bookkeeping day is closed.",
        "items": [
            {
                "title": "All documents received today are filed and processed",
                "mandatory": True,
                "auto_rule": "documents_processed",
            },
            {"title": "Extracted document data has been reviewed", "mandatory": False, "assigned_role": "accountant"},
            {
                "title": "Every transaction of the day is approved or rejected",
                "mandatory": True,
                "auto_rule": "transactions_decided",
            },
            {"title": "No critical validation problem remains", "mandatory": True, "auto_rule": "no_open_critical"},
            {"title": "Devices have synchronised", "mandatory": False, "auto_rule": "sync_clean"},
            {"title": "Daily report prepared and checked", "mandatory": False},
        ],
    },
    "cash_and_bank": {
        "name": "Cash and bank day-end",
        "description": "A short check of cash and bank movements. It is not a bank reconciliation.",
        "items": [
            {"title": "Cash on hand counted and agreed with the books", "mandatory": False},
            {"title": "Bank movements of the day have been entered", "mandatory": False},
            {
                "title": "Unusual or large payments have been looked at",
                "mandatory": False,
                "assigned_role": "accountant",
            },
        ],
    },
}
