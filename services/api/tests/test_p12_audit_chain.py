"""Phase 12: the tamper-evident audit chain - sealing, verification, and detection of three kinds of tampering."""

# ruff: noqa: F811
from __future__ import annotations

import uuid

import pytest
from artify_api.db import DbContext, app_engine, dispatcher_engine, transaction
from conftest import new_owner
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_p6_documents import backend  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import books  # noqa: F401  (books is a fixture)
from test_p10_bookkeeping import BK, task_body


def seal(batch: int = 1000) -> int:
    """Seal everything pending, like the CLI does: other tests leave thousands of events, so one batch may not reach this one."""
    total = 0
    while True:
        with transaction(dispatcher_engine(), DbContext()) as conn:
            n = int(conn.execute(text("SELECT app.audit_seal(:n)"), {"n": batch}).scalar_one())
        total += n
        if n < batch:
            return total


def integrity(actor):  # type: ignore[no-untyped-def]
    r = actor.get(f"/orgs/{actor.org_id}/audit-integrity")
    assert r.status_code == 200, r.text
    return r.json()


def org_events(owner_engine, org):  # type: ignore[no-untyped-def]
    with owner_engine.begin() as c:
        return c.execute(
            text("SELECT id, created_at FROM audit_events WHERE organization_id = :o ORDER BY created_at, id"),
            {"o": org},
        ).all()


def test_sealing_builds_a_chain_that_verifies_and_grows(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    seal()
    first = integrity(o)
    assert first["ok"] is True and first["checked"] > 0 and first["head_hash"] and first["unsealed_recent"] == 0
    assert seal() == 0  # nothing new: sealing is idempotent
    o.post(BK(a, "/tasks"), task_body())
    pending = integrity(o)
    assert pending["ok"] is True and pending["unsealed_recent"] >= 1  # honest: the new event is not protected yet
    assert seal() >= 1
    after = integrity(o)
    assert after["ok"] is True and after["checked"] > first["checked"] and after["head_hash"] != first["head_hash"]
    assert after["head_seq"] == after["checked"]  # seq is 1..n without gaps


def test_organizations_have_separate_chains_and_cannot_see_each_other(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o = books["owner"]
    other = new_owner(client, mailer, org_name="Other Audit Firm")
    seal()
    a, b = integrity(o), integrity(other)
    assert a["ok"] and b["ok"] and a["head_hash"] != b["head_hash"]
    # a tenant cannot verify somebody else's organization
    r = o.get(f"/orgs/{other.org_id}/audit-integrity")
    assert r.status_code in (403, 404)


def test_an_edited_event_is_detected(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    o.post(BK(a, "/tasks"), task_body())
    seal()
    assert integrity(o)["ok"]
    ev = org_events(owner_engine, o.org_id)[3]
    with owner_engine.begin() as c:  # an attacker with database access switches the immutability trigger off
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text("UPDATE audit_events SET summary = '{\"forged\": true}' WHERE id = :i AND created_at = :t"),
            {"i": ev.id, "t": ev.created_at},
        )
    bad = integrity(o)
    assert bad["ok"] is False and bad["reason"] == "event_modified" and bad["first_bad_seq"] == 4


def test_a_deleted_event_is_detected(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o = books["owner"]
    seal()
    assert integrity(o)["ok"]
    ev = org_events(owner_engine, o.org_id)[2]
    with owner_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text("DELETE FROM audit_events WHERE id = :i AND created_at = :t"), {"i": ev.id, "t": ev.created_at})
    bad = integrity(o)
    assert bad["ok"] is False and bad["reason"] == "event_missing" and bad["first_bad_seq"] == 3


def test_a_removed_or_reordered_seal_is_detected(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o = books["owner"]
    seal()
    assert integrity(o)["ok"]
    with owner_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text("DELETE FROM audit_seals WHERE organization_id = :o AND seq = 2"), {"o": o.org_id})
    bad = integrity(o)
    assert bad["ok"] is False and bad["reason"] == "sequence_gap" and bad["first_bad_seq"] == 2


def test_the_application_cannot_forge_or_change_seals(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o = books["owner"]
    seal()
    with owner_engine.begin() as c:
        for sql in (
            "UPDATE audit_seals SET row_hash = '\\x00' WHERE organization_id = :o",
            "DELETE FROM audit_seals WHERE organization_id = :o",
        ):
            c.execute(text("SAVEPOINT s"))
            try:
                c.execute(text(sql), {"o": o.org_id})
                raise AssertionError("seals can be changed")
            except AssertionError:
                raise
            except Exception:
                c.execute(text("ROLLBACK TO SAVEPOINT s"))
    # the request role has no way to call the sealing function either
    with pytest.raises(DBAPIError), transaction(app_engine(), DbContext(org_id=uuid.UUID(o.org_id))) as conn:
        conn.execute(text("SELECT app.audit_seal(10)"))
