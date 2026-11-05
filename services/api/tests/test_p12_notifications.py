"""Phase 12: notification delivery - outbox events become in-app notifications and e-mails, privately and exactly once."""

# ruff: noqa: F811
from __future__ import annotations

from typing import Any

from artify_api import mailer as mailer_mod
from artify_api.event_tasks import handle_event
from artify_api.outbox import dispatch_pending
from conftest import new_owner, uid
from sqlalchemy import text
from test_p6_documents import backend  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import books  # noqa: F401  (books is a fixture)
from test_p10_bookkeeping import BK, full_close, quiet, task_body, txn

TITLE = "Chase missing invoice"


def deliver() -> list[str]:
    """What the worker does for every pending outbox event; returns the handler results (errors are raised)."""
    results: list[str] = []

    def send(ev: dict[str, Any]) -> None:
        event = {k: (v if k == "payload" or v is None else str(v)) for k, v in ev.items()}
        results.append(handle_event(event))

    dispatch_pending(send, batch=500)
    return results


def mine(actor, **q):  # type: ignore[no-untyped-def]
    r = actor.get("/notifications" + ("?" + "&".join(f"{k}={v}" for k, v in q.items()) if q else ""))
    assert r.status_code == 200, r.text
    return r.json()


def test_an_assignment_becomes_one_private_notification_and_one_email(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, mailer = books["owner"], books["a"], books["prep"], books["mailer"]
    deliver()  # clear earlier events
    before = len(mailer.sent)
    t = o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep))).json()
    assert mine(prep)["unread"] == 0  # nothing is delivered until the worker runs
    deliver()
    n = mine(prep)
    assert n["total"] == 1 and n["unread"] == 1
    item = n["items"][0]
    assert (
        item["event_type"] == "bookkeeping.task.assigned" and item["entity_id"] == t["id"] and item["read_at"] is None
    )
    assert item["email_state"] == "sent"
    sent = [m for m in mailer.sent[before:] if m.template == "notification"]
    assert len(sent) == 1 and sent[0].to.lower() == books["prep"].email.lower()
    # nothing the person typed is in the notification or the e-mail
    blob = f"{item['title']} {item['body']} {sent[0].context}"
    assert TITLE not in blob
    assert mine(o)["total"] == 0  # the actor and other people see none of it


def test_redelivery_never_duplicates(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, mailer = books["owner"], books["a"], books["prep"], books["mailer"]
    deliver()
    o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep)))
    with owner_engine.begin() as c:
        ev = (
            c.execute(
                text(
                    "SELECT id, organization_id, company_id, event_type, payload FROM outbox_events WHERE state = 'pending' AND event_type LIKE 'bookkeeping.%' ORDER BY created_at DESC LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
    event = {k: (v if k == "payload" else str(v)) for k, v in dict(ev).items()}
    first = handle_event(event)
    again = handle_event(event)
    assert first.endswith(":1") and again.endswith(":0")
    assert mine(prep)["total"] == 1
    assert len([m for m in mailer.sent if m.template == "notification" and m.to.lower() == prep.email.lower()]) == 1


def test_people_are_not_notified_of_their_own_actions_and_cannot_read_each_others(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    deliver()
    o.post(BK(a, "/tasks"), task_body(assignee_id=uid(o)))  # assigned to the person who made it
    o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep)))
    deliver()
    assert mine(o)["total"] == 0
    nid = mine(prep)["items"][0]["id"]
    assert o.post(f"/notifications/{nid}/read").status_code == 404
    assert acct.post(f"/notifications/{nid}/read").status_code == 404
    done = prep.post(f"/notifications/{nid}/read")
    assert done.status_code == 200 and done.json()["read_at"]
    assert mine(prep, unread="true")["total"] == 0 and mine(prep)["total"] == 1
    again = prep.post(f"/notifications/{nid}/read")
    assert again.status_code == 200 and again.json()["read_at"] == done.json()["read_at"]  # stays read


def test_email_can_be_turned_off_without_losing_the_in_app_notification(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, mailer = books["owner"], books["a"], books["prep"], books["mailer"]
    deliver()
    assert prep.get("/notifications/preferences").json() == {"email_enabled": True}
    assert prep.put("/notifications/preferences", {"email_enabled": False}).json() == {"email_enabled": False}
    before = len(mailer.sent)
    o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep)))
    deliver()
    n = mine(prep)["items"][0]
    assert n["email_state"] == "skipped" and not [m for m in mailer.sent[before:] if m.template == "notification"]
    assert prep.put("/notifications/preferences", {"email_enabled": True}).status_code == 200


def test_a_mail_failure_is_recorded_and_does_not_lose_the_notification(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]

    class Broken:
        def send(self, message: Any) -> None:
            raise OSError("smtp down")

    deliver()
    good = mailer_mod.get_mailer()
    mailer_mod.set_mailer(Broken())
    try:
        o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep)))
        deliver()
    finally:
        mailer_mod.set_mailer(good)
    n = mine(prep)["items"][0]
    assert n["email_state"] == "failed" and n["title"]


def test_closing_outcomes_reach_the_people_who_prepared_them(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    quiet(o, a)
    txn(books)
    deliver()
    full_close(books)  # prep prepares and submits, acct closes
    deliver()
    kinds = [i["event_type"] for i in mine(prep)["items"]]
    assert "bookkeeping.closing.closed" in kinds
    assert all(i["entity_type"] == "bk_closing" for i in mine(prep)["items"] if "closing" in i["event_type"])


def test_notifications_are_private_to_the_organization_and_cannot_be_rewritten(
    books, client, mailer, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    deliver()
    o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep)))
    deliver()
    stranger = new_owner(client, mailer, org_name="Other Firm")
    assert mine(stranger)["total"] == 0
    nid = mine(prep)["items"][0]["id"]
    assert stranger.post(f"/notifications/{nid}/read").status_code == 404
    with owner_engine.begin() as c:
        for sql in (
            "UPDATE notifications SET title = 'edited' WHERE id = :i",
            "DELETE FROM notifications WHERE id = :i",
        ):
            c.execute(text("SAVEPOINT s"))
            try:
                c.execute(text(sql), {"i": nid})
                raise AssertionError("the database allowed a change to a notification")
            except AssertionError:
                raise
            except Exception:
                c.execute(text("ROLLBACK TO SAVEPOINT s"))
        # a signed-in user context cannot touch delivery state
        c.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": uid(prep)})
        try:
            c.execute(text("SAVEPOINT s2"))
            c.execute(text("UPDATE notifications SET email_state = 'failed' WHERE id = :i"), {"i": nid})
            raise AssertionError("a user context changed delivery state")
        except AssertionError:
            raise
        except Exception:
            c.execute(text("ROLLBACK TO SAVEPOINT s2"))
