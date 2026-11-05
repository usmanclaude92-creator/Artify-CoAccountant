"""Phase 12: exporting audit heads outside the database, checking them later, and the platform audit chain."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest
from artify_api import cli
from artify_api.db import DbContext, dispatcher_engine, transaction
from conftest import new_owner, new_platform_admin
from sqlalchemy import text
from test_p6_documents import backend  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import books  # noqa: F401  (books is a fixture)
from test_p10_bookkeeping import BK, task_body


def seal_all() -> None:
    while True:
        with transaction(dispatcher_engine(), DbContext()) as conn:
            n = int(conn.execute(text("SELECT app.audit_seal(1000)")).scalar_one())
            n += int(conn.execute(text("SELECT app.platform_audit_seal(1000)")).scalar_one())
        if n < 1000:
            return


def run(argv: list[str]) -> int:
    return cli.main(argv)


def test_exported_heads_match_until_history_is_cut_or_rewritten(books, owner_engine, tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    o.post(BK(a, "/tasks"), task_body())
    seal_all()
    out = tmp_path / "heads.jsonl"
    assert run(["audit-heads", "--out", str(out)]) == 0
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    mine = next(x for x in lines if x["organization_id"] == o.org_id)
    assert mine["seq"] >= 1 and len(mine["hash"]) == 64
    assert run(["audit-check-anchors", str(out)]) == 0  # nothing changed: everything matches

    # more history is added: the old anchor still matches (chains only grow)
    o.post(BK(a, "/tasks"), task_body())
    seal_all()
    assert run(["audit-check-anchors", str(out)]) == 0

    # the NEWEST seals are cut off: the integrity check alone cannot see it, the exported anchor can
    fresh = tmp_path / "fresh.jsonl"
    assert run(["audit-heads", "--out", str(fresh)]) == 0
    head = next(json.loads(x) for x in fresh.read_text().splitlines() if json.loads(x)["organization_id"] == o.org_id)
    with owner_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text("DELETE FROM audit_seals WHERE organization_id = :o AND seq = :s"), {"o": o.org_id, "s": head["seq"]}
        )
    assert o.get(f"/orgs/{o.org_id}/audit-integrity").json()["ok"] is True, (
        "truncation of the tail is invisible without an anchor"
    )
    assert run(["audit-check-anchors", str(fresh)]) == 1
    assert "seal missing" in capsys.readouterr().out

    # a seal that exists but was rewritten differs
    with owner_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text("UPDATE audit_seals SET row_hash = '\\x00' WHERE organization_id = :o AND seq = :s"),
            {"o": o.org_id, "s": mine["seq"]},
        )
    assert run(["audit-check-anchors", str(out)]) == 1
    assert "hash differs" in capsys.readouterr().out


def test_the_request_role_cannot_read_heads_or_check_anchors(books) -> None:  # type: ignore[no-untyped-def]
    import uuid

    from artify_api.db import app_engine
    from sqlalchemy.exc import DBAPIError

    for sql in ("SELECT * FROM app.audit_heads()", "SELECT app.audit_seal_at(NULL, 1)"):
        with (
            pytest.raises(DBAPIError),
            transaction(app_engine(), DbContext(org_id=uuid.UUID(books["owner"].org_id))) as conn,
        ):
            conn.execute(text(sql))


def test_the_platform_audit_log_has_its_own_chain(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    new_owner(client, mailer, org_name="Platform Chain Firm")
    admin.get("/dashboard")
    seal_all()
    v = admin.get("/audit-integrity").json()
    assert v["ok"] is True and v["checked"] > 0 and v["head_hash"] and v["unsealed_recent"] == 0
    ev = None
    with owner_engine.begin() as c:
        ev = c.execute(
            text("SELECT id, created_at FROM platform_audit_events ORDER BY created_at, id LIMIT 1 OFFSET 1")
        ).first()
    assert ev is not None
    with owner_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text("UPDATE platform_audit_events SET reason = 'forged' WHERE id = :i AND created_at = :t"),
            {"i": ev.id, "t": ev.created_at},
        )
    bad = admin.get("/audit-integrity").json()
    assert bad["ok"] is False and bad["reason"] == "event_modified"
    # customers have no way into the platform chain
    firm = new_owner(client, mailer, org_name="Not Platform")
    assert firm.get("/platform/audit-integrity").status_code in (401, 403, 404)


def test_metrics_are_off_until_a_token_is_set_and_then_need_it(client, books, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from artify_api.settings import get_settings
    from pydantic import SecretStr

    s = get_settings()
    monkeypatch.setattr(s, "metrics_token", None)
    assert client.get("/api/v1/metrics").status_code == 404  # disabled by default
    monkeypatch.setattr(s, "metrics_token", SecretStr("scrape-token-for-tests"))
    assert client.get("/api/v1/metrics").status_code == 401
    assert client.get("/api/v1/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/api/v1/metrics", headers={"Authorization": "Bearer scrape-token-for-tests"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    body = r.text
    for name in (
        "artify_http_responses_total", "artify_process_uptime_seconds", "artify_database_up 1", "artify_outbox_events",
        "artify_outbox_oldest_pending_seconds", "artify_audit_unsealed_events", "artify_audit_oldest_unsealed_seconds",
    ):  # fmt: skip
        assert name in body, name
    # counts and ages only: no ids, names or e-mail addresses
    assert "@" not in body and books["owner"].org_id not in body
