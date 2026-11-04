#!/usr/bin/env python3
"""Synthetic RLS overhead baseline (Phase 1). Builds N organizations x M companies as the schema
owner, then times company listing as the real `artify_app` role under RLS versus the same query
as the owner with an explicit WHERE clause (no RLS). Uses a disposable database.

usage: bench_rls.py <db> [orgs=100] [companies_per_org=500] [assigned=300]
Reads PG* env for the owner connection; login roles use the dev-only passwords."""
from __future__ import annotations

import os
import statistics
import sys
import time

import psycopg

db = sys.argv[1]
orgs, per_org, assigned = (int(x) for x in (sys.argv[2:5] + ["100", "500", "300"][len(sys.argv[2:5]):]))
host, port, user = os.environ.get("PGHOST", "/var/run/postgresql"), os.environ.get("PGPORT", "54329"), os.environ.get("PGUSER", "postgres")
common = {"host": host, "port": port, "dbname": db}

with psycopg.connect(user=user, password=os.environ.get("PGPASSWORD"), autocommit=True, **common) as owner:
    owner.execute("TRUNCATE companies, organizations CASCADE")
    owner.execute(
        "INSERT INTO organizations (id, type, legal_name, display_name, slug, country, default_currency)"
        " SELECT gen_random_uuid(), 'accounting_firm', 'o'||g, 'o'||g, 'bench-'||g, 'OM', 'OMR' FROM generate_series(1, %s) g", (orgs,))
    owner.execute(
        "INSERT INTO companies (id, organization_id, name, code, country, base_currency)"
        " SELECT gen_random_uuid(), o.id, 'c'||g, 'C'||g, 'OM', 'OMR' FROM organizations o, generate_series(1, %s) g", (per_org,))
    owner.execute("ANALYZE companies")
    org = owner.execute("SELECT id FROM organizations ORDER BY slug LIMIT 1").fetchone()[0]
    ids = [r[0] for r in owner.execute("SELECT id FROM companies WHERE organization_id = %s LIMIT %s", (org, assigned))]
    total = owner.execute("SELECT count(*) FROM companies").fetchone()[0]

    def timeit(conn, sql, params, setup=None, runs=30):
        samples = []
        for _ in range(runs):
            with conn.transaction():
                if setup:
                    conn.execute(*setup)
                t0 = time.perf_counter()
                n = len(conn.execute(sql, params).fetchall())
                samples.append((time.perf_counter() - t0) * 1000)
        return statistics.median(samples), max(samples), n

    app = psycopg.connect(user="artify_app_login", password="dev-only-app", **common)
    ctx_all = ("SELECT set_config('app.org_id', %s, true), set_config('app.company_scope', 'all', true)", (str(org),))
    ctx_list = ("SELECT set_config('app.org_id', %s, true), set_config('app.company_scope', 'list', true),"
                " set_config('app.company_ids', %s, true)", (str(org), ",".join(map(str, ids))))
    sql = "SELECT id, name FROM companies WHERE organization_id = %s ORDER BY name"
    rows = [
        ("no RLS (owner, explicit WHERE), all companies of 1 org", timeit(owner, sql, (org,))),
        ("RLS scope=all (firm owner)", timeit(app, sql, (org,), ctx_all)),
        (f"RLS scope=list ({len(ids)} assigned ids)", timeit(app, sql, (org,), ctx_list)),
        ("RLS point lookup by id (scope=list)", timeit(app, "SELECT id FROM companies WHERE id = %s", (ids[0],), ctx_list)),
    ]
    print(f"{total} companies in {orgs} orgs; org under test has {per_org}")
    print(f"{'scenario':58s} {'median ms':>10s} {'max ms':>9s} {'rows':>6s}")
    for name, (med, mx, n) in rows:
        print(f"{name:58s} {med:10.2f} {mx:9.2f} {n:6d}")
