"""Permission catalog and system role definitions (code is the source of truth; the database
copy is synchronized by `artify-api seed`).

Scopes:
  organization  held through an Organization role
  company       held through an Organization role over a company in scope, or a Company role
  member        satisfied by any active role the caller holds in the Organization
  platform      Platform Admin roles (separate realm)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    key: str
    scope: str
    description: str


_P = Permission
PERMISSIONS: tuple[Permission, ...] = (
    _P("organization.read", "organization", "View organization profile"),
    _P("organization.update", "organization", "Edit organization profile"),
    _P("members.read", "organization", "List organization members"),
    _P("members.manage", "organization", "Change roles / deactivate organization members"),
    _P("invitations.manage", "organization", "Invite and revoke organization invitations"),
    _P("audit.read", "organization", "Read the organization audit log"),
    _P("entitlements.read", "organization", "View subscription limits and usage"),
    _P("companies.create", "organization", "Create client companies"),
    _P("companies.list", "member", "List companies visible to the caller"),
    _P("companies.read", "company", "View a company"),
    _P("companies.update", "company", "Edit a company"),
    _P("company_members.read", "company", "List a company's client-side members"),
    _P("company_members.manage", "company", "Invite / deactivate a company's client-side members"),
    _P("assignments.read", "company", "List accountant assignments"),
    _P("assignments.manage", "company", "Assign / unassign accountants"),
    _P("devices.manage_own", "member", "Register and revoke the caller's own devices"),
    _P("support_grants.manage", "organization", "Approve, deny and revoke Artify support-access requests"),
    _P("organization.settings.update", "organization", "Edit organization contact, regional and notification settings"),
    _P("member_profiles.manage", "organization", "Edit team members' professional profiles"),
    _P("ownership.transfer", "organization", "Transfer organization ownership to another member"),
    _P("companies.archive", "company", "Archive, restore, activate and deactivate a client company"),
    _P(
        "company_settings.manage", "company", "Edit a company's accounting settings (financial year, VAT, jurisdiction)"
    ),
    _P("workspace.read", "company", "View a company's workspace structure, categories, fields and rules"),
    _P(
        "workspace.manage",
        "company",
        "Change a company's workspace structure, categories, fields, rules and apply templates",
    ),
    _P("workspace.permissions.manage", "company", "Manage who can see and change which parts of a company's workspace"),
    _P(
        "workspace_templates.manage", "organization", "Save, version and archive the organization's workspace templates"
    ),
    # ---- platform realm (Artify Super Admin) ----
    _P("platform.self.read", "platform", "Read own platform-admin profile"),
    _P("platform.dashboard.read", "platform", "View the platform overview"),
    _P("platform.organizations.read", "platform", "View organizations (metadata and aggregates only)"),
    _P("platform.organizations.manage", "platform", "Create organizations and edit their profile"),
    _P("platform.organizations.suspend", "platform", "Suspend and reactivate organizations"),
    _P("platform.companies.read", "platform", "View the platform-wide company overview (metadata only)"),
    _P("platform.plans.read", "platform", "View subscription plans"),
    _P("platform.plans.manage", "platform", "Create plans, publish versions, activate and archive plans"),
    _P("platform.subscriptions.read", "platform", "View subscriptions and their history"),
    _P("platform.subscriptions.manage", "platform", "Change plan, extend, suspend and reactivate subscriptions"),
    _P("platform.entitlements.override", "platform", "Grant or expire per-organization limit overrides"),
    _P("platform.billing.read", "platform", "View invoices and payment records"),
    _P("platform.billing.manage", "platform", "Create invoices, record payments, refunds and adjustments"),
    _P("platform.usage.read", "platform", "View usage against limits"),
    _P("platform.admins.read", "platform", "View platform administrators"),
    _P("platform.admins.manage", "platform", "Invite, re-role, suspend and revoke platform administrators"),
    _P("platform.support.read", "platform", "View support-access requests and grants"),
    _P("platform.support.request", "platform", "Request customer-authorized support access"),
    _P("platform.support.view_data", "platform", "Use an active support grant to view tenant metadata"),
    _P("platform.support.revoke", "platform", "Revoke any support grant"),
    _P("platform.audit.read", "platform", "Read the platform audit log and activity feed"),
    _P("platform.health.read", "platform", "View platform health"),
    _P("platform.settings.read", "platform", "View platform settings"),
    _P("platform.settings.manage", "platform", "Change platform settings"),
)
PERMISSION_KEYS = frozenset(p.key for p in PERMISSIONS)
_BY_KEY = {p.key: p for p in PERMISSIONS}


def permission_scope(key: str) -> str:
    return _BY_KEY[key].scope


ORG_ADMIN_PERMS = frozenset(
    k
    for k in PERMISSION_KEYS
    if not k.startswith("platform.") and k not in ("organization.update", "support_grants.manage", "ownership.transfer")
)

# role key -> (scope, display name, permissions)
SYSTEM_ROLES: dict[str, tuple[str, str, frozenset[str]]] = {
    "firm_owner": (
        "organization",
        "Firm owner",
        frozenset(k for k in PERMISSION_KEYS if not k.startswith("platform.")),
    ),
    "practice_admin": ("organization", "Practice administrator", ORG_ADMIN_PERMS),
    "practice_manager": (
        "organization",
        "Practice manager",
        frozenset(
            {
                "organization.read",
                "members.read",
                "invitations.manage",
                "audit.read",
                "entitlements.read",
                "companies.create",
                "companies.list",
                "companies.read",
                "companies.update",
                "company_settings.manage",
                "company_members.read",
                "company_members.manage",
                "assignments.read",
                "assignments.manage",
                "devices.manage_own",
                "workspace.read",
                "workspace.manage",
                "workspace_templates.manage",
            }
        ),
    ),
    "accountant": (
        "organization",
        "Accountant",
        frozenset(
            {
                "organization.read",
                "members.read",
                "companies.list",
                "companies.read",
                "company_members.read",
                "assignments.read",
                "devices.manage_own",
                "workspace.read",
            }
        ),
    ),
    "assistant_accountant": (
        "organization",
        "Assistant accountant",
        frozenset(
            {
                "organization.read",
                "companies.list",
                "companies.read",
                "company_members.read",
                "assignments.read",
                "devices.manage_own",
                "workspace.read",
            }
        ),
    ),
    "staff": (
        "organization",
        "Staff",
        frozenset({"organization.read", "companies.list", "companies.read", "devices.manage_own", "workspace.read"}),
    ),
    "document_operator": (
        "organization",
        "Document operator",
        frozenset({"organization.read", "companies.list", "companies.read", "devices.manage_own", "workspace.read"}),
    ),
    "read_only": (
        "organization",
        "Read-only user",
        frozenset({"organization.read", "companies.list", "companies.read", "workspace.read"}),
    ),
    "company_owner": (
        "company",
        "Company owner",
        frozenset(
            {"companies.list", "companies.read", "company_members.read", "company_members.manage", "devices.manage_own"}
        ),
    ),
    "company_admin": (
        "company",
        "Company administrator",
        frozenset(
            {"companies.list", "companies.read", "company_members.read", "company_members.manage", "devices.manage_own"}
        ),
    ),
    "company_employee": (
        "company",
        "Company employee",
        frozenset({"companies.list", "companies.read", "devices.manage_own"}),
    ),
}

# Organization roles that reach every Company of the Organization.
ALL_COMPANY_ROLES = frozenset({"firm_owner", "practice_admin", "practice_manager"})
# Roles whose holders must complete MFA (docs/security/SECURITY_ARCHITECTURE.md §2).
MFA_REQUIRED_ROLES = frozenset({"firm_owner", "practice_admin", "practice_manager"})
# Roles that may be assigned through invitations (firm_owner is created only at signup).
INVITABLE_ORG_ROLES = frozenset(
    {
        "practice_admin",
        "practice_manager",
        "accountant",
        "assistant_accountant",
        "document_operator",
        "read_only",
        "staff",
    }
)
# Roles counted by `limit.accountants` (professional staff). Keep in step with app.measure_entitlement (migration 0005).
ACCOUNTANT_ROLES = frozenset({"accountant", "assistant_accountant", "staff"})
# Organization roles that can be assigned to specific client companies: everyone who does NOT reach all companies.
# Keep in step with app.check_assignment_member (migration 0005).
ASSIGNABLE_ROLES = frozenset({"accountant", "assistant_accountant", "document_operator", "read_only", "staff"})
# Seniority of organization roles. A caller may invite or grant only roles at or below their own rank, never
# firm_owner (that moves only through an ownership transfer).
ORG_ROLE_RANK = {
    "firm_owner": 100,
    "practice_admin": 80,
    "practice_manager": 60,
    "accountant": 40,
    "assistant_accountant": 30,
    "staff": 30,
    "document_operator": 20,
    "read_only": 10,
}
INVITABLE_COMPANY_ROLES = frozenset({"company_owner", "company_admin", "company_employee"})
# Company roles a company_admin may grant/change/remove (never owner or admin: no lateral escalation).
COMPANY_ADMIN_MANAGEABLE_ROLES = frozenset({"company_employee"})

_PLATFORM_ALL = frozenset(k for k in PERMISSION_KEYS if k.startswith("platform."))
_READ = frozenset(
    {
        "platform.self.read",
        "platform.dashboard.read",
        "platform.organizations.read",
        "platform.companies.read",
        "platform.plans.read",
        "platform.subscriptions.read",
        "platform.billing.read",
        "platform.usage.read",
        "platform.support.read",
        "platform.audit.read",
        "platform.health.read",
        "platform.settings.read",
        "platform.admins.read",
    }
)

# Least privilege: no role except the owner holds everything; sensitive powers are separate keys.
PLATFORM_ROLES: dict[str, frozenset[str]] = {
    "platform_owner": _PLATFORM_ALL,
    "platform_admin": _PLATFORM_ALL
    - {"platform.admins.manage", "platform.billing.manage", "platform.settings.manage", "platform.support.view_data"},
    "platform_support": frozenset(
        {
            "platform.self.read",
            "platform.dashboard.read",
            "platform.organizations.read",
            "platform.companies.read",
            "platform.subscriptions.read",
            "platform.usage.read",
            "platform.support.read",
            "platform.support.request",
            "platform.support.view_data",
            "platform.health.read",
        }
    ),
    "platform_billing": frozenset(
        {
            "platform.self.read",
            "platform.dashboard.read",
            "platform.organizations.read",
            "platform.plans.read",
            "platform.subscriptions.read",
            "platform.billing.read",
            "platform.billing.manage",
            "platform.usage.read",
        }
    ),
    "platform_auditor": frozenset({"platform.self.read", "platform.audit.read", "platform.health.read"}),
    "platform_readonly": _READ,
}

# Ranking used to stop administrators from granting roles above their own.
PLATFORM_ROLE_RANK = {
    "platform_owner": 100,
    "platform_admin": 80,
    "platform_support": 40,
    "platform_billing": 40,
    "platform_readonly": 20,
    "platform_auditor": 20,
}
