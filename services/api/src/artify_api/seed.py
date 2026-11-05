"""Idempotent reference-data synchronization (run after migrations, and on every deploy).

Runs with the schema-owner credential (ARTIFY_DATABASE_URL_MIGRATE), which must be a superuser or
BYPASSRLS operations role: RLS is FORCEd on these tables, so service roles cannot write them.
Code is the source of truth for permissions, system roles and entitlement definitions. The
default plan is created only if missing, so plan edits made later in Super Admin are never overwritten.
"""

from __future__ import annotations

import json

from sqlalchemy import Connection, text

from artify_api.entitlements import DEFINITIONS
from artify_api.ids import new_id
from artify_api.permissions import PERMISSIONS, SYSTEM_ROLES
from artify_api.workspace_engine import DOCUMENT_KINDS
from artify_api.workspace_templates_data import SYSTEM_TEMPLATES

# ISO 4217 minor units (subset needed initially; extend by adding rows, not code paths).
CURRENCIES = (
    ("OMR", "Omani Rial", 3),
    ("USD", "US Dollar", 2),
    ("EUR", "Euro", 2),
    ("GBP", "Pound Sterling", 2),
    ("AED", "UAE Dirham", 2),
    ("SAR", "Saudi Riyal", 2),
    ("QAR", "Qatari Riyal", 2),
    ("KWD", "Kuwaiti Dinar", 3),
    ("BHD", "Bahraini Dinar", 3),
    ("JOD", "Jordanian Dinar", 3),
    ("EGP", "Egyptian Pound", 2),
    ("INR", "Indian Rupee", 2),
    ("PKR", "Pakistani Rupee", 2),
)

# Development default plan. Real commercial plans are created in Super Admin (Phase 2).
DEV_PLAN_KEY = "development_trial"
DEV_PLAN_LIMITS: dict[str, int | bool] = {
    "limit.companies": 5,
    "limit.users": 20,
    "limit.accountants": 10,
    "limit.devices": 20,
    "limit.workspace_sections_per_company": 12,
    "limit.folder_depth": 6,
    "limit.folders_per_company": 200,
    "limit.custom_fields_per_company": 30,
    "limit.filing_rules_per_company": 50,
    "limit.workspace_templates": 10,
    "limit.excel_templates_per_company": 10,
    "quota.documents_per_month": 500,
    "quota.storage_bytes": 5 * 1024**3,
    "limit.file_size_bytes": 50 * 1024**2,
    "quota.ocr_pages_per_month": 2000,
    "quota.ai_extractions_per_month": 500,
    "quota.bank_statements_per_month": 60,
    "limit.processing_concurrency": 4,
    "feature.advanced_ai": False,
    "quota.accounting_transactions_per_month": 2000,
    "quota.journals_per_month": 2000,
    "limit.accounting_rules_per_company": 100,
    "feature.multi_currency": True,
    "feature.advanced_approval": True,
    "feature.advanced_accounting": True,
    "feature.ai_accounting_suggestions": False,
    "quota.ai_units_per_month": 2000,
    "feature.custom_roles": False,
    "feature.api_access": False,
    "feature.report_builder": True,
    "feature.macro_templates": False,
    "feature.cloud_ai": False,
}


def sync_reference_data(conn: Connection) -> None:
    for code, name, minor in CURRENCIES:
        conn.execute(
            text(
                "INSERT INTO currencies (code, name, minor_unit) VALUES (:c, :n, :m)"
                " ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, minor_unit = EXCLUDED.minor_unit"
            ),
            {"c": code, "n": name, "m": minor},
        )
    for i, k in enumerate(DOCUMENT_KINDS):
        conn.execute(
            text(
                "INSERT INTO document_kinds (key, name, description, accounting_domain, processing_type, creates_financial_records, sort_order)"
                " VALUES (:k, :n, :d, :ad, :pt, :cf, :s) ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name,"
                " description = EXCLUDED.description, accounting_domain = EXCLUDED.accounting_domain,"
                " processing_type = EXCLUDED.processing_type, creates_financial_records = EXCLUDED.creates_financial_records,"
                " sort_order = EXCLUDED.sort_order"
            ),
            {
                "k": k.key,
                "n": k.name,
                "d": k.description,
                "ad": k.accounting_domain,
                "pt": k.processing_type,
                "cf": k.creates_financial_records,
                "s": i,
            },
        )
    _sync_system_templates(conn)
    for d in DEFINITIONS:
        conn.execute(
            text(
                "INSERT INTO entitlement_definitions (key, scope, value_type, enforcement, unit, description)"
                " VALUES (:k, :s, :t, :e, :u, :d) ON CONFLICT (key) DO UPDATE SET scope = EXCLUDED.scope,"
                " value_type = EXCLUDED.value_type, enforcement = EXCLUDED.enforcement, unit = EXCLUDED.unit,"
                " description = EXCLUDED.description"
            ),
            {"k": d.key, "s": d.scope, "t": d.value_type, "e": d.enforcement, "u": d.unit, "d": d.description},
        )
    for p in PERMISSIONS:
        conn.execute(
            text(
                "INSERT INTO permissions (key, scope, description) VALUES (:k, :s, :d)"
                " ON CONFLICT (key) DO UPDATE SET scope = EXCLUDED.scope, description = EXCLUDED.description"
            ),
            {"k": p.key, "s": p.scope, "d": p.description},
        )
    for key, (scope, name, perms) in SYSTEM_ROLES.items():
        role_id = conn.execute(
            text(
                "INSERT INTO roles (id, key, scope, name, is_system) VALUES (:id, :k, :s, :n, true)"
                " ON CONFLICT (key) WHERE organization_id IS NULL DO UPDATE SET name = EXCLUDED.name,"
                " scope = EXCLUDED.scope RETURNING id"
            ),
            {"id": new_id(), "k": key, "s": scope, "n": name},
        ).scalar_one()
        conn.execute(
            text("DELETE FROM role_permissions WHERE role_id = :r AND NOT (permission_key = ANY(:keep))"),
            {"r": role_id, "keep": sorted(perms)},
        )
        for perm in sorted(perms):
            conn.execute(
                text("INSERT INTO role_permissions (role_id, permission_key) VALUES (:r, :p) ON CONFLICT DO NOTHING"),
                {"r": role_id, "p": perm},
            )

    plan_id = conn.execute(text("SELECT id FROM subscription_plans WHERE key = :k"), {"k": DEV_PLAN_KEY}).scalar()
    if plan_id is None:
        plan_id = new_id()
        version_id = new_id()
        conn.execute(
            text("INSERT INTO subscription_plans (id, key, name) VALUES (:id, :k, 'Development trial')"),
            {"id": plan_id, "k": DEV_PLAN_KEY},
        )
        conn.execute(
            text(
                "INSERT INTO subscription_plan_versions (id, plan_id, version, billing_interval)"
                " VALUES (:id, :p, 1, 'none')"
            ),
            {"id": version_id, "p": plan_id},
        )
        for key, value in DEV_PLAN_LIMITS.items():
            conn.execute(
                text(
                    "INSERT INTO plan_entitlements (plan_version_id, entitlement_key, int_value, bool_value)"
                    " VALUES (:v, :k, :i, :b)"
                ),
                {
                    "v": version_id,
                    "k": key,
                    "i": None if isinstance(value, bool) else value,
                    "b": value if isinstance(value, bool) else None,
                },
            )
    settings: tuple[tuple[str, str | int], ...] = (("default_plan_key", DEV_PLAN_KEY), ("trial_days", 30))
    for setting_key, setting_value in settings:
        conn.execute(
            text(
                "INSERT INTO platform_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) ON CONFLICT (key) DO NOTHING"
            ),
            {"k": setting_key, "v": json.dumps(setting_value)},
        )


def _sync_system_templates(conn: Connection) -> None:
    """Artify system workspace templates (organization_id NULL). A changed definition becomes a NEW version; existing
    versions and every company that applied them are never touched."""
    for key, (name, description, build) in SYSTEM_TEMPLATES.items():
        definition = build()
        definition.check()
        template_id = conn.execute(
            text("SELECT id FROM workspace_templates WHERE organization_id IS NULL AND key = :k"), {"k": key}
        ).scalar()
        if template_id is None:
            template_id = new_id()
            conn.execute(
                text(
                    "INSERT INTO workspace_templates (id, organization_id, key, name, description) VALUES (:id, NULL, :k, :n, :d)"
                ),
                {"id": template_id, "k": key, "n": name, "d": description},
            )
        else:
            conn.execute(
                text("UPDATE workspace_templates SET name = :n, description = :d WHERE id = :id"),
                {"n": name, "d": description, "id": template_id},
            )
        latest = conn.execute(
            text(
                "SELECT version, definition FROM workspace_template_versions WHERE template_id = :t ORDER BY version DESC LIMIT 1"
            ),
            {"t": template_id},
        ).first()
        new_def = json.loads(definition.model_dump_json())
        if latest is None or latest.definition != new_def:
            conn.execute(
                text(
                    "INSERT INTO workspace_template_versions (id, template_id, organization_id, version, definition, changelog)"
                    " VALUES (:id, :t, NULL, :v, CAST(:d AS jsonb), :c)"
                ),
                {
                    "id": new_id(),
                    "t": template_id,
                    "v": 1 if latest is None else latest.version + 1,
                    "d": json.dumps(new_def),
                    "c": "Initial version" if latest is None else "Updated by Artify",
                },
            )
