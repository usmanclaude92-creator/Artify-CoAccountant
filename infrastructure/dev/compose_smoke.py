#!/usr/bin/env python3
"""Smoke test for the Docker Compose stack (run after `docker compose up -d`).

Proves the chain across real containers: API + PostgreSQL (RLS) + Redis rate limiter +
outbox -> dispatcher -> Celery worker. Reads the e-mailed verification token from the API's
console-mailer log (development only). Exits non-zero on the first failed expectation."""
from __future__ import annotations

import re
import subprocess
import sys
import time
import uuid

import httpx
import pyotp

BASE = "http://localhost:8000/api/v1"
PASSWORD = "correct horse battery staple"


def logs(service: str) -> str:
    return subprocess.run(["docker", "compose", "logs", "--no-color", service], capture_output=True, text=True, check=False).stdout


def expect(cond: bool, what: str) -> None:
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        sys.exit(1)


def wait_for(predicate, timeout: float = 30.0) -> bool:  # type: ignore[no-untyped-def]
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(1)
    return False


c = httpx.Client(base_url=BASE, timeout=15)
email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
expect(c.get("/health/ready").json() == {"status": "ok"}, "API ready (database reachable from the API container)")

r = c.post("/auth/signup", json={"email": email, "password": PASSWORD, "display_name": "Smoke", "organization_name": "Compose Smoke Org",
                                  "organization_type": "accounting_firm", "country": "OM", "default_currency": "OMR"})
expect(r.status_code == 202, "sign-up accepted")
token = None
def find_token() -> bool:
    global token
    m = re.findall(r"MAIL to=" + re.escape(email) + r" template=verify_email context=\{\\?'token\\?': \\?'([^'\\]+)", logs("api"))
    token = m[-1] if m else None
    return token is not None
expect(wait_for(find_token), "verification e-mail produced (console mailer)")
expect(c.post("/auth/verify-email", json={"token": token}).status_code == 204, "e-mail verified")
login = c.post("/auth/login", json={"email": email, "password": PASSWORD}).json()
h = {"Authorization": f"Bearer {login['tokens']['access_token']}"}
org = c.get("/me", headers=h).json()["memberships"][0]["organization_id"]
h["X-Organization-Id"] = org
blocked = c.get(f"/orgs/{org}", headers=h)
expect(blocked.status_code == 403 and blocked.json()["code"] == "mfa_required", "firm owner blocked until MFA (policy engine in container)")
secret = c.post("/auth/mfa/enroll", headers=h).json()["secret"]
conf = c.post("/auth/mfa/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=h)
expect(conf.status_code == 200, "MFA enrolled")
h["Authorization"] = f"Bearer {conf.json()['access_token']}"
co = c.post(f"/orgs/{org}/companies", json={"name": "Smoke Co", "code": "SMOKE", "country": "OM", "base_currency": "OMR"}, headers=h)
expect(co.status_code == 201, "company created (entitlement guard + RLS + outbox write in one transaction)")
expect(wait_for(lambda: "handled:company.created" in logs("worker")), "outbox -> dispatcher -> Redis -> Celery worker handled company.created")
codes = [c.post("/auth/login", json={"email": f"nobody-{i}@example.com", "password": "x" * 12 + str(i)}).status_code for i in range(40)]
expect(429 in codes, "Redis-backed rate limiter throttles repeated logins")
j = logs("api").splitlines()
expect(any('"correlation_id"' in line and '"message": "request"' in line for line in j), "structured JSON request logs with correlation ids")
all_logs = logs("api") + logs("worker") + logs("dispatcher") + logs("migrate")
expect(PASSWORD not in all_logs, "the password never appears in any container log")
expect(token not in (logs("worker") + logs("dispatcher") + logs("migrate")), "the e-mailed token appears only in the API console-mailer log (local mode), not in worker/dispatcher logs")
print("\nCOMPOSE SMOKE PASSED")
