"""Seeds a REAL running Artify API for the desktop live test: an owner (with MFA), an accountant assigned to one company,
three companies with different workspaces. Usage: seed_live.py <api_base> <api_log> <out.json>. Synthetic data only."""

from __future__ import annotations

import json
import re
import sys
import time
import uuid

import httpx
import pyotp

BASE, LOG, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
PASSWORD = "correct horse battery staple"
tag = uuid.uuid4().hex[:8]


def mailed_token(email: str, template: str) -> str:
    pattern = re.compile(rf"MAIL to={re.escape(email)} template={template} context=\{{'token': '([^']+)'\}}")
    for _ in range(40):
        found = pattern.findall(open(LOG, encoding="utf-8").read())
        if found:
            return found[-1]
        time.sleep(0.25)
    raise SystemExit(f"no {template} mail for {email}")


c = httpx.Client(base_url=BASE, timeout=20)
owner_email = f"live-owner-{tag}@example.com"
r = c.post("/auth/signup", json={"email": owner_email, "password": PASSWORD, "display_name": "Live Owner", "organization_name": f"Desktop Live Firm {tag}", "organization_type": "accounting_firm", "country": "OM", "default_currency": "OMR"})
assert r.status_code == 202, r.text
assert c.post("/auth/verify-email", json={"token": mailed_token(owner_email, "verify_email")}).status_code == 204
login = c.post("/auth/login", json={"email": owner_email, "password": PASSWORD}).json()
h = {"Authorization": f"Bearer {login['tokens']['access_token']}"}
org = c.get("/me", headers=h).json()["memberships"][0]["organization_id"]
secret = c.post("/auth/mfa/enroll", headers=h).json()["secret"]
conf = c.post("/auth/mfa/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=h).json()
h = {"Authorization": f"Bearer {conf['access_token']}", "X-Organization-Id": org}


def post(path: str, body: dict | None = None, expect: int = 201):
    r = c.post(path, json=body, headers=h)
    assert r.status_code == expect, (path, r.status_code, r.text)
    return r.json()


def company(code: str, name: str) -> str:
    return post(f"/orgs/{org}/companies", {"name": name, "code": code, "country": "OM", "base_currency": "OMR"})["id"]


def node(cid: str, name: str, parent: str | None = None) -> str:
    return post(f"/companies/{cid}/workspace/nodes", {"name": name, "parent_id": parent})["item"]["id"]


a, b, cc = company(f"A{tag}", "Alpha Trading"), company(f"B{tag}", "Beta Contracting"), company(f"C{tag}", "Gamma Empty")
for n in ["Bank Statements", "Receivables", "Payables", "Expenses"]:
    node(a, n)
sections = [node(b, f"Section {i:02d}") for i in range(10)]
banking = node(b, "Banking", sections[0])
node(b, "Bank of Muscat", banking)
archived = node(b, "Old Folder", sections[1])
post(f"/companies/{b}/workspace/nodes/{archived}/archive", {"confirm_impact": True}, expect=200)

# an accountant who is assigned to company A only
acc_email = f"live-acc-{tag}@example.com"
post(f"/orgs/{org}/invitations", {"email": acc_email, "role": "accountant"})
r = c.post("/auth/invitations/accept", json={"token": mailed_token(acc_email, "invitation"), "display_name": "Live Accountant", "password": PASSWORD})
assert r.status_code == 200, r.text
members = c.get(f"/orgs/{org}/members", headers=h).json()
acc_id = next(m["user_id"] for m in members if m["email"] == acc_email)
post(f"/companies/{a}/assignments", {"user_id": acc_id, "assignment_type": "primary"}, expect=201)

json.dump({"api_base": BASE, "org": org, "owner": {"email": owner_email, "password": PASSWORD, "totp_secret": secret}, "accountant": {"email": acc_email, "password": PASSWORD, "user_id": acc_id},
           "companies": {"alpha": a, "beta": b, "gamma": cc}, "beta_section_ids": sections, "alpha_section_names": ["Bank Statements", "Receivables", "Payables", "Expenses"]}, open(OUT, "w"), indent=2)
print("seeded", OUT)
