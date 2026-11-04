"""API conventions: idempotency, commit-before-response, outbox/worker context, settings safety,
invitation edge cases, audit content."""

from __future__ import annotations

import uuid

import pytest
from artify_api import audit, outbox
from artify_api.context import RequestCtx, Requires
from artify_api.db import app_engine
from artify_api.errors import install_error_handlers
from artify_api.settings import Settings
from artify_api.worker import run_in_tenant_context
from conftest import PASSWORD, invite_and_accept, make_company, new_owner, uniq
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text


def test_idempotency_key_replays_and_rejects_mismatch(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    body = {"name": "Idem", "code": "IDEM", "country": "OM", "base_currency": "OMR"}
    key = {"Idempotency-Key": uuid.uuid4().hex}
    first = a.post(f"/orgs/{a.org_id}/companies", body, **key)
    again = a.post(f"/orgs/{a.org_id}/companies", body, **key)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert len(a.get(f"/orgs/{a.org_id}/companies").json()) == 1
    changed = a.post(f"/orgs/{a.org_id}/companies", {**body, "name": "Different"}, **key)
    assert changed.status_code == 422
    assert a.post(f"/orgs/{a.org_id}/companies", body, **{"Idempotency-Key": "short"}).status_code == 422


def test_duplicate_company_code_is_a_conflict_not_a_500(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    make_company(a, "DUP")
    r = a.post(f"/orgs/{a.org_id}/companies", {"name": "n", "code": "DUP", "country": "OM", "base_currency": "OMR"})
    assert r.status_code == 409
    # the same code in ANOTHER organization is fine
    b = new_owner(client, mailer)
    make_company(b, "DUP")
    bad_currency = a.post(
        f"/orgs/{a.org_id}/companies", {"name": "n", "code": "Z", "country": "OM", "base_currency": "ZZZ"}
    )
    assert bad_currency.status_code == 409


def test_error_format_is_problem_json_with_correlation_id(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/me", headers={"X-Request-ID": "trace-123"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.headers["x-request-id"] == "trace-123" and r.json()["correlation_id"] == "trace-123"
    assert r.headers["x-content-type-options"] == "nosniff"
    v = client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert v.status_code == 422 and v.json()["code"] == "validation_error"
    assert client.get("/api/v1/nope").status_code == 404


def test_transaction_commits_before_the_response_is_sent(owner_engine) -> None:  # type: ignore[no-untyped-def]
    """If the commit fails (deferred constraint) the client must see an error, never a 2xx.
    Guards the dependency scope="function" decision."""
    with owner_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS t_child; DROP TABLE IF EXISTS t_parent"))
        conn.execute(text("CREATE TABLE t_parent (id int PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE t_child (parent_id int REFERENCES t_parent(id) DEFERRABLE INITIALLY DEFERRED)"))
        conn.execute(text("GRANT INSERT, SELECT ON t_child, t_parent TO artify_app"))
    try:
        app = FastAPI()
        install_error_handlers(app)

        @app.post("/boom")
        def boom(ctx: RequestCtx = Depends(Requires(reason="test"))) -> dict[str, str]:
            ctx.conn.execute(text("INSERT INTO t_child (parent_id) VALUES (999)"))
            return {"status": "ok"}

        # need a real token: reuse the main app to log in
        from artify_api.main import create_app
        from conftest import signup_and_verify

        main = TestClient(create_app())
        r = main.post(
            "/api/v1/auth/signup",
            json={
                "email": uniq("c"),
                "password": PASSWORD,
                "display_name": "c",
                "organization_name": "Commit Org",
                "organization_type": "independent_accountant",
                "country": "OM",
                "default_currency": "OMR",
            },
        )
        assert r.status_code == 202
        del signup_and_verify
        # sign in using the memory mailer token path
        from artify_api.mailer import get_mailer

        email = get_mailer().sent[-1].to  # type: ignore[attr-defined]
        token = get_mailer().sent[-1].context["token"]  # type: ignore[attr-defined]
        main.post("/api/v1/auth/verify-email", json={"token": token})
        access = main.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["tokens"][
            "access_token"
        ]
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/boom", headers={"Authorization": f"Bearer {access}"})
        assert resp.status_code == 500, resp.text
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS t_child; DROP TABLE IF EXISTS t_parent"))


def test_audit_redacts_sensitive_values() -> None:
    redacted = audit.redact({"password": "x", "nested": {"refresh_token": "y", "ok": 1}, "list": [{"secret": "z"}]})
    assert redacted == {
        "password": "[redacted]",
        "nested": {"refresh_token": "[redacted]", "ok": 1},
        "list": [{"secret": "[redacted]"}],
    }


def test_audit_log_records_actions_and_is_tenant_scoped(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer), new_owner(client, mailer)
    make_company(a, "AUD")
    events = a.get(f"/orgs/{a.org_id}/audit-events").json()
    actions = {e["action"] for e in events}
    assert {"org.created", "user.signed_up", "company.created", "auth.mfa_enabled"} <= actions
    assert all("password" not in str(e["summary"]).lower() for e in events)
    assert not any(e["action"] == "company.created" for e in b.get(f"/orgs/{b.org_id}/audit-events").json())


def test_outbox_dispatch_and_failure_backoff(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    make_company(a, "OBX")  # emits company.created in the same transaction
    sent: list[dict] = []
    assert outbox.dispatch_pending(sent.append) >= 1
    assert any(e["event_type"] == "company.created" and str(e["organization_id"]) == a.org_id for e in sent)
    again: list[dict] = []
    outbox.dispatch_pending(again.append)
    assert not any(e["organization_id"] == a.org_id for e in again)  # already dispatched, not re-sent
    # failures back off instead of spinning
    with app_engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": a.org_id})
        ev = outbox.enqueue(conn, event_type="test.fail", org_id=uuid.UUID(a.org_id))

    def failing(_: dict) -> None:
        raise RuntimeError("broker down")

    outbox.dispatch_pending(failing)
    with owner_engine.connect() as conn:
        row = conn.execute(
            text("SELECT state, attempts, available_at > now() AS later FROM outbox_events WHERE id=:i"), {"i": ev}
        ).one()
    assert (row.state, row.attempts, row.later) == ("pending", 1, True)


def test_worker_context_is_limited_to_one_company(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    c1, _c2 = make_company(a, "W1"), make_company(a, "W2")

    def visible(conn: Connection) -> list[str]:
        return sorted(str(r[0]) for r in conn.execute(text("SELECT id FROM companies")))

    assert run_in_tenant_context(a.org_id, c1["id"], visible) == [c1["id"]]
    assert run_in_tenant_context(a.org_id, None, visible) == []  # no company scope => nothing
    other = new_owner(client, mailer)
    assert run_in_tenant_context(other.org_id, c1["id"], visible) == []  # wrong org => nothing

    def try_user_rows(conn: Connection) -> int:
        return conn.execute(text("SELECT count(*) FROM sessions")).scalar_one()

    assert run_in_tenant_context(a.org_id, c1["id"], try_user_rows) == 0  # system actor has no user identity


def test_invitation_edge_cases(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    email = uniq("inv")
    inv = a.post(f"/orgs/{a.org_id}/invitations", {"email": email, "role": "staff"}).json()
    token = mailer.last("invitation", email).context["token"]
    assert client.post("/api/v1/auth/invitations/accept", json={"token": "x" * 20}).status_code == 422
    assert (
        client.post("/api/v1/auth/invitations/accept", json={"token": token}).status_code == 422
    )  # needs password+name
    weak = client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "display_name": "S", "password": "weak"}
    )
    assert weak.status_code == 422
    ok = client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "display_name": "S", "password": PASSWORD}
    )
    assert ok.status_code == 200
    assert (
        client.post(
            "/api/v1/auth/invitations/accept", json={"token": token, "display_name": "S", "password": PASSWORD}
        ).status_code
        == 422
    )
    # revoked invitations cannot be used
    email2 = uniq("rev")
    inv2 = a.post(f"/orgs/{a.org_id}/invitations", {"email": email2, "role": "staff"}).json()
    assert a.delete(f"/orgs/{a.org_id}/invitations/{inv2['id']}").status_code == 204
    t2 = mailer.last("invitation", email2).context["token"]
    assert (
        client.post(
            "/api/v1/auth/invitations/accept", json={"token": t2, "display_name": "S", "password": PASSWORD}
        ).status_code
        == 422
    )
    assert inv["role"] == "staff"
    # firm_owner cannot be invited
    assert a.post(f"/orgs/{a.org_id}/invitations", {"email": uniq(), "role": "firm_owner"}).status_code == 422


def test_existing_user_must_authenticate_to_accept_an_invitation(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    staff = invite_and_accept(a, mailer, role="staff")
    b = new_owner(client, mailer)  # a different firm invites the same person
    r = b.post(f"/orgs/{b.org_id}/invitations", {"email": staff.email, "role": "accountant"})
    assert r.status_code == 201
    token = mailer.last("invitation", staff.email).context["token"]
    anon = client.post("/api/v1/auth/invitations/accept", json={"token": token})
    assert anon.status_code == 401
    stranger = client.post(
        "/api/v1/auth/invitations/accept", json={"token": token}, headers={"Authorization": f"Bearer {b.access}"}
    )
    assert stranger.status_code == 401  # a different signed-in user cannot accept it
    mine = client.post(
        "/api/v1/auth/invitations/accept", json={"token": token}, headers={"Authorization": f"Bearer {staff.access}"}
    )
    assert mine.status_code == 200
    memberships = staff.get("/me").json()["memberships"]
    assert {m["organization_id"] for m in memberships} == {a.org_id, b.org_id}
    # one identity, two tenants: access in each is independent
    assert staff.get(f"/orgs/{b.org_id}", org=b.org_id).status_code == 200
    assert staff.get(f"/orgs/{a.org_id}", org=a.org_id).status_code == 200


def test_production_settings_reject_development_secrets() -> None:
    with pytest.raises(ValueError, match="real secret"):
        Settings(
            environment="production",
            rate_limit_backend="redis",
            database_url_app="postgresql+psycopg://u:p@h/db",
            database_url_platform="postgresql+psycopg://u:p@h/db",
            database_url_dispatcher="postgresql+psycopg://u:p@h/db",
        )
    with pytest.raises(ValueError, match="differ"):
        Settings(tenant_jwt_secret="s" * 40, platform_jwt_secret="s" * 40)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least 32"):
        Settings(tenant_jwt_secret="short")  # type: ignore[arg-type]
