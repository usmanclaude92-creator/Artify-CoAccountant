"""Structural guards for the Phase 3 role catalog and the permission each privileged route declares.

These exist because two behavioural mutations ("company settings need only companies.update", "drop the member-role rank
rule") survived the behavioural tests: with TODAY'S catalog they are equivalent mutants (no role holds one permission
without the other). The tests below fail the moment the catalog or a route declaration changes in a way that would make
the difference matter, so the behaviour then has to be tested explicitly.
"""

from __future__ import annotations

from artify_api.context import Requires
from artify_api.permissions import ORG_ROLE_RANK, SYSTEM_ROLES
from conftest import iter_api_routes, walk_dependencies


def _required(method: str, path: str) -> str | None:
    for full, route in iter_api_routes():
        if full == f"/api/v1{path}" and method in route.methods:
            for call in walk_dependencies(route.dependant):
                if isinstance(call, Requires):
                    return call.permission
    raise AssertionError(f"route {method} {path} not found")


def test_privileged_company_routes_declare_their_own_permission() -> None:
    assert _required("POST", "/companies/{company_id}/status") == "companies.archive"
    assert _required("PATCH", "/companies/{company_id}/settings") == "company_settings.manage"
    assert _required("PATCH", "/companies/{company_id}") == "companies.update"
    assert _required("POST", "/orgs/{org_id}/ownership-transfer") == "ownership.transfer"
    assert _required("PATCH", "/orgs/{org_id}/settings") == "organization.settings.update"
    assert _required("PATCH", "/orgs/{org_id}/members/{user_id}") == "members.manage"
    assert _required("POST", "/orgs/{org_id}/invitations") == "invitations.manage"


def test_catalog_keeps_the_equivalence_the_behavioural_tests_rely_on() -> None:
    # 1. whoever may edit a company may edit its accounting settings and lifecycle, so a route that wrongly asked
    #    for companies.update instead would not be caught by a role-based behavioural test. If this ever changes,
    #    add a behavioural test for the split.
    for key, (_, _, perms) in SYSTEM_ROLES.items():
        if "companies.update" in perms:
            assert {"company_settings.manage"} <= perms, f"{key} can edit companies but not their settings: add a test"
    # 2. only roles ranked 80 or higher can change other members' roles. The rank rule on member updates is
    #    therefore only reachable by owner/administrator, who are covered by the firm-owner rules.
    for key, (_, _, perms) in SYSTEM_ROLES.items():
        if "members.manage" in perms:
            assert ORG_ROLE_RANK[key] >= 80, f"{key} has members.manage below rank 80: test the rank rule for it"
