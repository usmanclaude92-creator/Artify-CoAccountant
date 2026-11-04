"""Test harness. Tests run against a REAL PostgreSQL (RLS cannot be meaningfully mocked).

Connection comes from the standard PG* variables (default: the local dev cluster on a unix
socket, port 54329). The database `artify_test` is dropped and rebuilt (migrate + roles + seed)
once per test session via infrastructure/database/reset_dev_db.sh.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pyotp
import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
PGHOST = os.environ.get("PGHOST", "/var/run/postgresql")
PGPORT = os.environ.get("PGPORT", "54329")
PGUSER = os.environ.get("PGUSER", "postgres")
DB = "artify_test"


def _url(user: str, password: str | None) -> str:
    cred = f"{user}:{password}@" if password else f"{user}@"
    if PGHOST.startswith("/"):
        return f"postgresql+psycopg://{cred}/{DB}?host={PGHOST}&port={PGPORT}"
    return f"postgresql+psycopg://{cred}{PGHOST}:{PGPORT}/{DB}"


OWNER_URL = _url(PGUSER, os.environ.get("PGPASSWORD"))
os.environ.update(
    {
        "ARTIFY_DATABASE_URL_APP": _url("artify_app_login", "dev-only-app"),
        "ARTIFY_DATABASE_URL_PLATFORM": _url("artify_platform_login", "dev-only-platform"),
        "ARTIFY_DATABASE_URL_DISPATCHER": _url("artify_dispatcher_login", "dev-only-dispatcher"),
        "ARTIFY_DATABASE_URL_MIGRATE": OWNER_URL,
        "ARTIFY_MAILER_BACKEND": "memory",
        "ARTIFY_RATE_LIMIT_BACKEND": "memory",
        "ARTIFY_ENVIRONMENT": "ci",
    }
)

from artify_api import ratelimit  # noqa: E402
from artify_api.db import dispose_engines  # noqa: E402
from artify_api.mailer import MemoryMailer, set_mailer  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    env = {**os.environ, "PGHOST": PGHOST, "PGPORT": PGPORT, "PGUSER": PGUSER}
    if os.environ.get("ARTIFY_TEST_SKIP_RESET") == "1":  # developer speed-up / mutation checks only
        yield
        dispose_engines()
        return
    subprocess.run(
        [str(ROOT / "infrastructure/database/reset_dev_db.sh"), DB], check=True, env=env, capture_output=True, text=True
    )
    yield
    dispose_engines()


@pytest.fixture(autouse=True)
def _fresh_limiter_and_mailer() -> Iterator[MemoryMailer]:
    ratelimit.set_limiter(ratelimit.MemoryLimiter())
    mailer = MemoryMailer()
    set_mailer(mailer)
    yield mailer
    ratelimit.set_limiter(None)
    set_mailer(None)


@pytest.fixture(scope="session")
def owner_engine():  # type: ignore[no-untyped-def]
    """Schema-owner connection (superuser in dev/CI): for assertions and fixtures only."""
    eng = create_engine(OWNER_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def mailer(_fresh_limiter_and_mailer: MemoryMailer) -> MemoryMailer:
    return _fresh_limiter_and_mailer


@pytest.fixture
def client() -> Iterator[TestClient]:
    from artify_api.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


PASSWORD = "correct horse battery staple"


def uniq(prefix: str = "u") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def totp_now(secret: str, offset_steps: int = 0) -> str:
    import time

    return pyotp.TOTP(secret).at(time.time() + 30 * offset_steps)


class Actor:
    """A signed-in test user."""

    def __init__(
        self,
        client: TestClient,
        email: str,
        access: str,
        refresh: str,
        org_id: str | None = None,
        mfa_secret: str | None = None,
    ) -> None:
        self.client, self.email, self.access, self.refresh = client, email, access, refresh
        self.org_id, self.mfa_secret = org_id, mfa_secret

    def headers(self, org: str | None = None, **extra: str) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.access}", **extra}
        if org or self.org_id:
            h["X-Organization-Id"] = org or self.org_id or ""
        return h

    def get(self, path: str, org: str | None = None, **kw):  # type: ignore[no-untyped-def]
        return self.client.get(f"/api/v1{path}", headers=self.headers(org), **kw)

    def post(self, path: str, json: dict | None = None, org: str | None = None, **extra):  # type: ignore[no-untyped-def]
        return self.client.post(f"/api/v1{path}", json=json, headers=self.headers(org, **extra))

    def patch(self, path: str, json: dict, org: str | None = None):  # type: ignore[no-untyped-def]
        return self.client.patch(f"/api/v1{path}", json=json, headers=self.headers(org))

    def delete(self, path: str, org: str | None = None):  # type: ignore[no-untyped-def]
        return self.client.delete(f"/api/v1{path}", headers=self.headers(org))


def signup_and_verify(
    client: TestClient,
    mailer: MemoryMailer,
    *,
    email: str | None = None,
    org_name: str = "Test Firm",
    org_type: str = "accounting_firm",
) -> str:
    email = email or uniq("owner")
    r = client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Owner",
            "organization_name": org_name,
            "organization_type": org_type,
            "country": "OM",
            "default_currency": "OMR",
        },
    )
    assert r.status_code == 202, r.text
    token = mailer.last("verify_email", email).context["token"]
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 204
    return email


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def new_owner(
    client: TestClient, mailer: MemoryMailer, *, org_name: str = "Test Firm", org_type: str = "accounting_firm"
) -> Actor:
    """Signed-up, verified owner who has completed MFA enrollment (as required for this role)."""
    email = signup_and_verify(client, mailer, org_name=org_name, org_type=org_type)
    body = login(client, email)
    actor = Actor(client, email, body["tokens"]["access_token"], body["tokens"]["refresh_token"])
    org_id = actor.get("/me").json()["memberships"][0]["organization_id"]
    actor.org_id = org_id
    enroll = client.post("/api/v1/auth/mfa/enroll", headers=actor.headers())
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    confirm = client.post("/api/v1/auth/mfa/confirm", json={"code": totp_now(secret)}, headers=actor.headers())
    assert confirm.status_code == 200, confirm.text
    actor.access, actor.mfa_secret = confirm.json()["access_token"], secret
    return actor


def invite_and_accept(
    owner: Actor, mailer: MemoryMailer, *, role: str, path: str | None = None, email: str | None = None
) -> Actor:
    """Invite a new user (org role or, via `path`, a company role) and accept as that user."""
    email = email or uniq(role)
    org = owner.org_id
    target = path or f"/orgs/{org}/invitations"
    r = owner.post(target, {"email": email, "role": role})
    assert r.status_code == 201, r.text
    token = mailer.last("invitation", email).context["token"]
    r = owner.client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "display_name": role.title(), "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    body = login(owner.client, email)
    actor = Actor(owner.client, email, body["tokens"]["access_token"], body["tokens"]["refresh_token"], org)
    return actor


def make_company(owner: Actor, code: str, name: str | None = None, **kw: str) -> dict:
    r = owner.post(
        f"/orgs/{owner.org_id}/companies",
        {"name": name or f"Company {code}", "code": code, "country": "OM", "base_currency": "OMR", **kw},
    )
    assert r.status_code == 201, r.text
    return r.json()


def set_plan_limit(
    owner_engine,
    org_id: str,
    key: str,
    value: int | None,
    *,  # type: ignore[no-untyped-def]
    unlimited: bool = False,
    expires: str | None = None,
) -> None:
    """Platform-side override (what Super Admin will do in Phase 2) for entitlement tests."""
    with owner_engine.begin() as conn:
        sub = conn.execute(text("SELECT id FROM subscriptions WHERE organization_id = :o"), {"o": org_id}).scalar_one()
        conn.execute(
            text(
                "INSERT INTO entitlement_overrides (id, organization_id, subscription_id, entitlement_key,"
                " int_value, unlimited, reason, expires_at) VALUES (gen_random_uuid(), :o, :s, :k, :v, :u,"
                " 'test', CAST(:e AS timestamptz))"
            ),
            {"o": org_id, "s": sub, "k": key, "v": None if unlimited else value, "u": unlimited, "e": expires},
        )
