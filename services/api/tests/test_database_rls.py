"""Database-level isolation guarantees (TS-ISO-04, TS-ISO-05, TS-ISO-06, TS-SEC-04, schema lint).

These run as the real service roles; they prove the database refuses what a buggy API might try."""

from __future__ import annotations

import os
import uuid

import pytest
from conftest import make_company, new_owner
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


@pytest.fixture(scope="module")
def app_engine():  # type: ignore[no-untyped-def]
    eng = create_engine(os.environ["ARTIFY_DATABASE_URL_APP"])
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def platform_engine():  # type: ignore[no-untyped-def]
    eng = create_engine(os.environ["ARTIFY_DATABASE_URL_PLATFORM"])
    yield eng
    eng.dispose()


def _ctx(conn, user=None, org=None, scope=None, companies=""):  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            "SELECT set_config('app.user_id', :u, true), set_config('app.org_id', :o, true),"
            " set_config('app.company_scope', :s, true), set_config('app.company_ids', :c, true)"
        ),
        {"u": str(user or ""), "o": str(org or ""), "s": scope or "", "c": companies},
    )


def test_service_roles_cannot_bypass_rls(owner_engine) -> None:  # type: ignore[no-untyped-def]
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN"
                " ('artify_app','artify_platform','artify_dispatcher','artify_definer',"
                "  'artify_app_login','artify_platform_login','artify_dispatcher_login')"
            )
        ).all()
    assert len(rows) == 7
    assert not any(r.rolsuper or r.rolbypassrls for r in rows)


def test_empty_context_sees_nothing_and_cannot_write(app_engine, client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """TS-ISO-04: fail closed."""
    new_owner(client, mailer)  # make sure rows exist
    with app_engine.connect() as conn, conn.begin():
        for table in (
            "organizations",
            "users",
            "companies",
            "organization_memberships",
            "audit_events",
            "sessions",
            "subscriptions",
            "invitations",
            "devices",
            "usage_counters",
        ):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
    with pytest.raises(DBAPIError), app_engine.connect() as conn, conn.begin():
        conn.execute(
            text(
                "INSERT INTO organizations (id,type,legal_name,display_name,slug,country,default_currency)"
                " VALUES (gen_random_uuid(),'accounting_firm','x','x','x-1','OM','OMR')"
            )
        )


def test_org_context_only_sees_own_org_and_cannot_insert_for_another(app_engine, client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="Org A"), new_owner(client, mailer, org_name="Org B")
    with app_engine.connect() as conn, conn.begin():
        _ctx(conn, org=a.org_id)
        seen = {str(r[0]) for r in conn.execute(text("SELECT id FROM organizations"))}
        assert seen == {a.org_id}
    with pytest.raises(DBAPIError), app_engine.connect() as conn, conn.begin():
        _ctx(conn, org=a.org_id, scope="all")
        conn.execute(
            text(
                "INSERT INTO companies (id, organization_id, name, code, country, base_currency)"
                " VALUES (gen_random_uuid(), :o, 'x', 'x', 'OM', 'OMR')"
            ),
            {"o": b.org_id},
        )


def test_composite_fk_blocks_cross_company_reference(app_engine, owner_engine, client, mailer) -> None:  # type: ignore[no-untyped-def]
    """TS-ISO-05: even with an authorized API call, a row cannot point at another org's company."""
    a, b = new_owner(client, mailer, org_name="FK A"), new_owner(client, mailer, org_name="FK B")
    company_b = make_company(b, "FKB")
    with owner_engine.connect() as conn:
        user_a = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": a.email}).scalar_one()
    with pytest.raises(DBAPIError) as exc, app_engine.connect() as conn, conn.begin():
        _ctx(conn, user=user_a, org=a.org_id, scope="all")
        role = conn.execute(text("SELECT id FROM roles WHERE key='company_employee'")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO company_memberships (id, organization_id, company_id, user_id, role_id)"
                " VALUES (gen_random_uuid(), :o, :c, :u, :r)"
            ),
            {"o": a.org_id, "c": company_b["id"], "u": user_a, "r": role},
        )
    assert "foreign key" in str(exc.value).lower()


def test_audit_tables_are_append_only(app_engine, owner_engine, client, mailer) -> None:  # type: ignore[no-untyped-def]
    """TS-SEC-04: app role has no UPDATE/DELETE; even the owner/superuser hits the trigger."""
    a = new_owner(client, mailer, org_name="Audit A")
    for stmt in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError), app_engine.connect() as conn, conn.begin():
            _ctx(conn, org=a.org_id, scope="all")
            conn.execute(text(stmt))
        with pytest.raises(DBAPIError) as exc, owner_engine.connect() as conn, conn.begin():
            conn.execute(text(stmt))
        assert "append-only" in str(exc.value)
    with pytest.raises(DBAPIError) as exc, owner_engine.connect() as conn, conn.begin():
        conn.execute(text("TRUNCATE audit_events"))
    assert "append-only" in str(exc.value)


def test_platform_role_cannot_read_tenant_business_data(platform_engine, client, mailer) -> None:  # type: ignore[no-untyped-def]
    """TS-ISO-06 (database level): platform role sees tenant *metadata* only."""
    a = new_owner(client, mailer, org_name="Meta A")
    make_company(a, "META1")
    with platform_engine.connect() as conn, conn.begin():
        assert conn.execute(text("SELECT count(*) FROM organizations")).scalar() >= 1
        assert conn.execute(text("SELECT count(*) FROM subscriptions")).scalar() >= 1
    for table in ("companies", "company_memberships", "invitations", "devices"):
        with pytest.raises(DBAPIError) as exc, platform_engine.connect() as conn, conn.begin():
            conn.execute(text(f"SELECT count(*) FROM {table}"))
        assert "permission denied" in str(exc.value).lower(), table
    # Phase 2: members and user rows are readable ONLY through an active, customer-approved support grant.
    # With no grant the policies return zero rows even though rows exist (test_phase2_support.py covers the grant).
    for table in ("organization_memberships", "users"):
        with platform_engine.connect() as conn, conn.begin():
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
    # the password hash column of platform_admins is never readable by the platform role
    with pytest.raises(DBAPIError) as exc, platform_engine.connect() as conn, conn.begin():
        conn.execute(text("SELECT password_hash FROM platform_admins"))
    assert "permission denied" in str(exc.value).lower()


def test_tenant_cannot_read_other_tenants_plan_or_write_subscriptions(app_engine, client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer, org_name="Plan A")
    with app_engine.connect() as conn, conn.begin():
        _ctx(conn, org=a.org_id)
        assert conn.execute(text("SELECT count(*) FROM subscription_plan_versions")).scalar() == 1
    with pytest.raises(DBAPIError), app_engine.connect() as conn, conn.begin():
        _ctx(conn, org=a.org_id)
        conn.execute(text("UPDATE subscriptions SET status = 'active'"))


def test_every_table_is_classified_and_protected(owner_engine) -> None:  # type: ignore[no-untyped-def]
    """CI gate from docs/database/ISOLATION_AND_RLS.md §6: new tables fail until classified."""
    reference_tables = {
        "currencies",
        "entitlement_definitions",
        "permissions",
        "role_permissions",
        "alembic_version",
        "document_kinds",
    }
    with owner_engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
                " AND c.relkind IN ('r','p') AND NOT c.relispartition"
            )
        ).all()
        policies = {r[0] for r in conn.execute(text("SELECT DISTINCT tablename FROM pg_policies"))}
    assert tables
    for name, rls, force in tables:
        if name in reference_tables:
            continue
        assert rls and force, f"{name}: RLS must be enabled and forced"
        assert name in policies, f"{name}: no policies defined"


def test_tenant_tables_carry_tenant_columns(owner_engine) -> None:  # type: ignore[no-untyped-def]
    """Company-owned tables must have organization_id AND company_id and the composite FK."""
    with owner_engine.connect() as conn:
        cols = {}
        for t, c in conn.execute(
            text("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'")
        ):
            cols.setdefault(t, set()).add(c)
        composite_fks = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT conrelid::regclass::text FROM pg_constraint WHERE contype='f'"
                    " AND confrelid = 'companies'::regclass AND array_length(conkey,1) = 2"
                )
            )
        }
    for table, columns in cols.items():
        # Append-only logs (and their partitions) deliberately omit the FK so they outlive purges.
        log_tables = ("usage_records", "audit_events", "outbox_events")
        if table.endswith("_default") or table in log_tables:
            continue
        if "company_id" in columns:
            assert "organization_id" in columns, table
            assert table in composite_fks, f"{table}: missing composite FK to companies"


def test_no_floating_point_columns(owner_engine) -> None:  # type: ignore[no-untyped-def]
    """ADR-008 schema lint: financial values never use float/real/double/money."""
    with owner_engine.connect() as conn:
        bad = conn.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns"
                " WHERE table_schema='public' AND data_type IN ('real','double precision','money')"
            )
        ).all()
    assert not bad, bad


def test_definer_functions_enforce_context(app_engine) -> None:  # type: ignore[no-untyped-def]
    other = uuid.uuid4()
    with pytest.raises(DBAPIError) as exc, app_engine.connect() as conn, conn.begin():
        _ctx(conn, org=uuid.uuid4())
        conn.execute(text("SELECT app.measure_entitlement(:o, 'limit.companies')"), {"o": other})
    assert "context mismatch" in str(exc.value)


def test_company_scope_policy_is_planned_once_per_query(app_engine, client, mailer) -> None:  # type: ignore[no-untyped-def]
    """Performance regression guard: the company list must be an InitPlan (evaluated once), not a
    per-row function call (65 ms vs 1.5 ms for 300 companies in infrastructure/dev/bench_rls.py)."""
    a = new_owner(client, mailer, org_name="Plan Shape")
    with app_engine.connect() as conn, conn.begin():
        _ctx(conn, org=a.org_id, scope="list", companies=",".join(str(uuid.uuid4()) for _ in range(5)))
        plan = "\n".join(r[0] for r in conn.execute(text("EXPLAIN SELECT id FROM companies")))
    assert "InitPlan" in plan, plan
    assert "can_see_company" not in plan, plan


def test_phase3_tables_are_isolated_between_organizations_and_companies(
    app_engine, owner_engine, client, mailer
) -> None:  # type: ignore[no-untyped-def]
    """Added after a mutation check showed `member_profiles` could be opened to every organization without any test
    failing. Both Phase 3 tables must show a caller only their own organization (and, for configurations, their own
    company scope)."""
    a, b = new_owner(client, mailer, org_name="P3 RLS A"), new_owner(client, mailer, org_name="P3 RLS B")
    ca1, ca2 = make_company(a, "RA1"), make_company(a, "RA2")
    make_company(b, "RB1")
    # a professional profile in each organization, written through the API like a user would
    for owner in (a, b):
        me = owner.get("/me").json()
        res = owner.patch(f"/orgs/{owner.org_id}/members/{me['user_id']}/profile", {"job_title": "Partner"})
        assert res.status_code == 200, res.text

    with app_engine.connect() as conn, conn.begin():  # organization B, all companies
        _ctx(conn, org=b.org_id, scope="all")
        assert {str(r[0]) for r in conn.execute(text("SELECT DISTINCT organization_id FROM member_profiles"))} == {
            b.org_id
        }
        assert {
            str(r[0]) for r in conn.execute(text("SELECT DISTINCT organization_id FROM company_configurations"))
        } == {b.org_id}
    with app_engine.connect() as conn, conn.begin():  # no organization context: nothing at all
        _ctx(conn)
        assert conn.execute(text("SELECT count(*) FROM member_profiles")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM company_configurations")).scalar_one() == 0
    with app_engine.connect() as conn, conn.begin():  # organization A restricted to one company
        _ctx(conn, org=a.org_id, scope="assigned", companies=ca1["id"])
        seen = {str(r[0]) for r in conn.execute(text("SELECT company_id FROM company_configurations"))}
        assert seen == {ca1["id"]} and ca2["id"] not in seen
    with (
        pytest.raises(DBAPIError),
        app_engine.connect() as conn,
        conn.begin(),
    ):  # cannot write into another organization
        _ctx(conn, org=a.org_id, scope="all")
        conn.execute(
            text(
                "INSERT INTO member_profiles (organization_id, user_id, job_title) VALUES (:o, gen_random_uuid(), 'x')"
            ),
            {"o": b.org_id},
        )
