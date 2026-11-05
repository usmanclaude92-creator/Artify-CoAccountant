"""Measured response times of the main read paths on a modest data set. Not a load test and not a capacity claim: one process,
one thread, the development database on the same machine. Skipped unless ARTIFY_PERF=1 (it seeds a few hundred records):

    ARTIFY_PERF=1 uv run pytest tests/test_p12_perf.py -s -o addopts=
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import pytest
from test_p6_documents import backend, pdf, upload  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import books  # noqa: F401  (books is a fixture)
from test_p10_bookkeeping import BK, DAY, quiet, txn

pytestmark = pytest.mark.skipif(
    os.environ.get("ARTIFY_PERF") != "1", reason="set ARTIFY_PERF=1 to run the measurements"
)

DOCS, TXNS, REPEAT = 150, 60, 30


def timed(fn, n: int = REPEAT) -> dict:  # type: ignore[no-untyped-def,type-arg]
    ms = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = fn()
        ms.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200, r.text
    ms.sort()
    return {
        "n": n,
        "p50_ms": round(statistics.median(ms), 1),
        "p95_ms": round(ms[int(0.95 * (n - 1))], 1),
        "max_ms": round(ms[-1], 1),
    }


def test_measure_read_paths(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    quiet(o, a)
    t0 = time.perf_counter()
    for n in range(DOCS):
        upload(o, a, pdf(f"perf-{n}" * (n % 7 + 3)), f"perf{n}.pdf", books["node"])
    seed_docs = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(TXNS):
        txn(books)
    seed_txns = time.perf_counter() - t0
    results = {
        "data": {
            "documents": DOCS,
            "approved_transactions": TXNS,
            "seed_documents_s": round(seed_docs, 1),
            "seed_transactions_s": round(seed_txns, 1),
        },
        "GET /me": timed(lambda: o.get("/me")),
        "GET documents?limit=50": timed(lambda: o.get(f"/companies/{a}/documents?limit=50")),
        "GET documents?q=perf1": timed(lambda: o.get(f"/companies/{a}/documents?q=perf1&limit=50")),
        "GET sync/changes?limit=200": timed(lambda: o.get(f"/companies/{a}/sync/changes?limit=200")),
        "GET accounting/transactions?limit=50": timed(
            lambda: o.get(f"/companies/{a}/accounting/transactions?limit=50")
        ),
        "GET bookkeeping/days/{day}": timed(lambda: o.get(BK(a, f"/days/{DAY}"))),
        "GET bookkeeping/days/{day}/validation": timed(lambda: o.get(BK(a, f"/days/{DAY}/validation"))),
        "GET bookkeeping/days/{day}/cash-bank": timed(lambda: o.get(BK(a, f"/days/{DAY}/cash-bank"))),
    }
    out = Path(os.environ.get("ARTIFY_PERF_OUT", "artify_perf.json"))
    out.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
