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
    _P("platform.self.read", "platform", "Read own platform-admin profile"),
    _P("platform.organizations.read", "platform", "List organizations (metadata only)"),
    _P("platform.audit.read", "platform", "Read the platform audit log"),
)
PERMISSION_KEYS = frozenset(p.key for p in PERMISSIONS)
_BY_KEY = {p.key: p for p in PERMISSIONS}


def permission_scope(key: str) -> str:
    return _BY_KEY[key].scope


ORG_ADMIN_PERMS = frozenset(k for k in PERMISSION_KEYS if not k.startswith("platform.") and k != "organization.update")

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
INVITABLE_COMPANY_ROLES = frozenset({"company_owner", "company_employee"})

PLATFORM_ROLES: dict[str, frozenset[str]] = {
    "platform_owner": frozenset({"platform.self.read", "platform.organizations.read", "platform.audit.read"}),
    "platform_admin": frozenset({"platform.self.read", "platform.organizations.read", "platform.audit.read"}),
    "platform_support": frozenset({"platform.self.read", "platform.organizations.read"}),
    "platform_billing": frozenset({"platform.self.read", "platform.organizations.read"}),
    "platform_auditor": frozenset({"platform.self.read", "platform.audit.read"}),
}
