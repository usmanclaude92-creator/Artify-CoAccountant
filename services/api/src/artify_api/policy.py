"""Authorization policy engine: a pure function over an already-resolved principal.

decision = role permissions  ∩  scope (org-wide / assigned / own company)  ∩  permission scope.
Section permissions, resource state and entitlements join this chain in later phases.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from artify_api.permissions import (
    ALL_COMPANY_ROLES,
    MFA_REQUIRED_ROLES,
    SYSTEM_ROLES,
    permission_scope,
)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str  # stable code recorded in audit for denials


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    org_role: str | None = None
    # company_id -> company role key (client-side memberships)
    company_roles: dict[uuid.UUID, str] = field(default_factory=dict)
    assigned_company_ids: frozenset[uuid.UUID] = frozenset()
    mfa_verified: bool = False

    @property
    def company_scope(self) -> str:
        return "all" if self.org_role in ALL_COMPANY_ROLES else "list"

    @property
    def visible_company_ids(self) -> frozenset[uuid.UUID]:
        return self.assigned_company_ids | frozenset(self.company_roles)

    def can_reach_company(self, company_id: uuid.UUID) -> bool:
        return self.company_scope == "all" or company_id in self.visible_company_ids

    @property
    def requires_mfa(self) -> bool:
        return self.org_role in MFA_REQUIRED_ROLES


def _org_perms(p: Principal) -> frozenset[str]:
    return SYSTEM_ROLES[p.org_role][2] if p.org_role else frozenset()


def authorize(p: Principal, permission: str, company_id: uuid.UUID | None = None) -> Decision:
    if p.requires_mfa and not p.mfa_verified:
        return Decision(False, "mfa_required")
    scope = permission_scope(permission)
    if scope == "platform":
        return Decision(False, "wrong_realm")

    if scope == "organization":
        return Decision(permission in _org_perms(p), "role_lacks_permission")

    if scope == "member":
        if permission in _org_perms(p):
            return Decision(True, "ok")
        for role_key in p.company_roles.values():
            if permission in SYSTEM_ROLES[role_key][2]:
                return Decision(True, "ok")
        return Decision(False, "role_lacks_permission")

    # scope == "company"
    if company_id is None:
        return Decision(False, "company_required")
    if not p.can_reach_company(company_id):
        return Decision(False, "company_out_of_scope")
    if permission in _org_perms(p):
        return Decision(True, "ok")
    company_role = p.company_roles.get(company_id)
    if company_role and permission in SYSTEM_ROLES[company_role][2]:
        return Decision(True, "ok")
    return Decision(False, "role_lacks_permission")
