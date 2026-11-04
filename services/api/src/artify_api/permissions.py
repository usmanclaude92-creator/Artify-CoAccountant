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
    if not k.startswith("platform.") and k not in ("organization.update", "support_grants.manage")
)

# role key -> (scope, display name, permissions)
SYSTEM_ROLES: dict[str, tuple[str, str, frozenset[str]]] = {
    "firm_owner": (
        "organization",
        "Firm owner",
        frozenset(k for k in PERMISSION_KEYS if not k.startswith("platform.")),
    ),
    "practice_admin": ("organization", "Practice administrator", ORG_ADMIN_PERMS),
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
            }
        ),
    ),
    "staff": (
        "organization",
        "Staff",
        frozenset({"organization.read", "companies.list", "companies.read", "devices.manage_own"}),
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
ALL_COMPANY_ROLES = frozenset({"firm_owner", "practice_admin"})
# Roles whose holders must complete MFA (docs/security/SECURITY_ARCHITECTURE.md §2).
MFA_REQUIRED_ROLES = frozenset({"firm_owner", "practice_admin"})
# Roles that may be assigned through invitations (firm_owner is created only at signup).
INVITABLE_ORG_ROLES = frozenset({"practice_admin", "accountant", "staff"})
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
