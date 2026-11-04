"""Billing records: provider-independent, manual, append-only, no fabricated transactions."""

from __future__ import annotations

import pytest
from conftest import new_owner, new_platform_admin
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _org(admin, name):  # type: ignore[no-untyped-def]
    return next(o for o in admin.get("/organizations", q=name).json()["items"] if o["display_name"] == name)


def _invoice(admin, org_id, amount="100.000", currency="OMR", issue=True):  # type: ignore[no-untyped-def]
    r = admin.post(
        "/billing/invoices",
        {
            "organization_id": org_id,
            "amount_total": amount,
            "currency": currency,
            "issue": issue,
            "description": "Test subscription period",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_no_payment_or_invoice_exists_unless_an_administrator_recorded_it(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, "platform_billing")
    new_owner(client, mailer, org_name="Fresh Billing Firm")
    org = _org(admin, "Fresh Billing Firm")
    assert admin.get("/billing/invoices", organization_id=org["id"]).json()["total"] == 0
    assert admin.get("/billing/payments", organization_id=org["id"]).json()["total"] == 0
    summary = admin.get("/billing/summary").json()
    assert summary["provider"] is None and "No payment provider" in summary["note"]
    with owner_engine.connect() as conn:  # the schema holds no card or credential columns at all
        cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name IN ('billing_invoices','billing_payments','billing_adjustments')"
                )
            )
        }
    assert not {c for c in cols if any(w in c for w in ("card", "pan", "cvv", "secret", "token", "api_key"))}


def test_invoice_payment_lifecycle_and_balances(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, "platform_billing")
    new_owner(client, mailer, org_name="Paying Firm")
    org = _org(admin, "Paying Firm")
    draft = _invoice(admin, org["id"], issue=False)
    assert draft["status"] == "draft" and draft["number"].startswith("INV-") and draft["outstanding"] == "0.000"
    assert (
        admin.post(f"/billing/invoices/{draft['id']}/payments", {"amount": "10", "method": "cash"}).status_code == 409
    )  # not issued
    inv = admin.post(f"/billing/invoices/{draft['id']}/issue").json()
    assert inv["status"] == "open" and inv["outstanding"] == "100.000" and inv["issued_at"]
    assert admin.post(f"/billing/invoices/{inv['id']}/issue").status_code == 409
    # partial, then overpayment refused, then final payment closes it
    p1 = admin.post(
        f"/billing/invoices/{inv['id']}/payments", {"amount": "40.250", "method": "bank_transfer", "reference": "TRX-1"}
    )
    assert (
        p1.status_code == 201
        and p1.json()["invoice"]["outstanding"] == "59.750"
        and p1.json()["invoice"]["status"] == "open"
    )
    assert admin.post(f"/billing/invoices/{inv['id']}/payments", {"amount": "60", "method": "cash"}).status_code == 422
    assert admin.post(f"/billing/invoices/{inv['id']}/payments", {"amount": "0", "method": "cash"}).status_code == 422
    paid = admin.post(f"/billing/invoices/{inv['id']}/payments", {"amount": "59.750", "method": "cheque"}).json()[
        "invoice"
    ]
    assert paid["status"] == "paid" and paid["outstanding"] == "0.000" and paid["paid_total"] == "100.000"
    # refund reopens the balance; a credit adjustment can close it again
    refunded = admin.post(
        f"/billing/invoices/{inv['id']}/refunds",
        {"amount": "20", "method": "bank_transfer", "note": "Duplicate charge refunded"},
    ).json()
    assert refunded["invoice"]["status"] == "open" and refunded["invoice"]["outstanding"] == "20.000"
    assert [p["kind"] for p in refunded["payments"]] == ["payment", "payment", "refund"]
    assert (
        admin.post(
            f"/billing/invoices/{inv['id']}/refunds",
            {"amount": "500", "method": "cash", "note": "More than was ever paid"},
        ).status_code
        == 422
    )
    adj = admin.post(
        f"/billing/invoices/{inv['id']}/adjustments", {"amount": "-20", "reason": "Goodwill credit for downtime"}
    ).json()
    assert (
        adj["invoice"]["status"] == "paid"
        and adj["invoice"]["outstanding"] == "0.000"
        and adj["adjustments"][0]["amount"] == "-20.000"
    )
    assert admin.post(
        f"/billing/invoices/{inv['id']}/adjustments", {"amount": "-9999", "reason": "Would go negative"}
    ).status_code in (409, 422)
    assert (
        admin.post(f"/billing/invoices/{inv['id']}/void", {"reason": "Trying to void a paid invoice"}).status_code
        == 409
    )
    # lists, filters, summary (per currency, never mixed)
    assert admin.get("/billing/invoices", organization_id=org["id"], status="paid").json()["total"] == 1
    assert admin.get("/billing/invoices", q=inv["number"]).json()["total"] == 1
    assert admin.get("/billing/payments", organization_id=org["id"]).json()["total"] == 3
    summary = admin.get("/billing/summary").json()
    assert all(set(t) == {"currency", "amount"} for t in summary["collected_last_30_days"])
    assert len({t["currency"] for t in summary["collected_last_30_days"]}) == len(summary["collected_last_30_days"])


def test_void_and_overdue(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, "platform_billing")
    new_owner(client, mailer, org_name="Voiding Firm")
    org = _org(admin, "Voiding Firm")
    inv = _invoice(admin, org["id"], "50")
    assert admin.post(f"/billing/invoices/{inv['id']}/void", {"reason": "short"}).status_code == 422
    voided = admin.post(f"/billing/invoices/{inv['id']}/void", {"reason": "Issued to the wrong organization"}).json()
    assert voided["status"] == "void" and voided["outstanding"] == "0.000"
    assert admin.post(f"/billing/invoices/{inv['id']}/payments", {"amount": "5", "method": "cash"}).status_code == 409
    late = admin.post(
        "/billing/invoices",
        {
            "organization_id": org["id"],
            "amount_total": "30",
            "currency": "OMR",
            "issue": True,
            "due_at": "2020-01-01T00:00:00Z",
        },
    ).json()
    assert late["overdue"] is True
    assert late["id"] in {i["id"] for i in admin.get("/billing/invoices", overdue=True).json()["items"]}
    # voiding with a payment recorded is refused
    ok = _invoice(admin, org["id"], "25")
    admin.post(f"/billing/invoices/{ok['id']}/payments", {"amount": "5", "method": "cash"})
    assert admin.post(f"/billing/invoices/{ok['id']}/void", {"reason": "Cannot void with payments"}).status_code == 409


def test_money_validation_and_currency(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, "platform_billing")
    new_owner(client, mailer, org_name="Money Firm")
    org = _org(admin, "Money Firm")
    base = {"organization_id": org["id"], "currency": "OMR"}
    assert admin.post("/billing/invoices", {**base, "amount_total": "-1"}).status_code == 422
    assert admin.post("/billing/invoices", {**base, "amount_total": "1.2345"}).status_code == 422  # > 3 decimal places
    assert admin.post("/billing/invoices", {**base, "amount_total": "abc"}).status_code == 422
    assert admin.post("/billing/invoices", {**base, "amount_total": "1", "currency": "ZZZ"}).status_code == 422
    assert (
        admin.post(
            "/billing/invoices",
            {**base, "amount_total": "1", "organization_id": "00000000-0000-0000-0000-000000000000"},
        ).status_code
        == 404
    )
    assert (
        admin.post(
            "/billing/invoices",
            {**base, "amount_total": "1", "period_start": "2026-02-01T00:00:00Z", "period_end": "2026-01-01T00:00:00Z"},
        ).status_code
        == 422
    )
    inv = _invoice(admin, org["id"], "12.345", "OMR")
    assert inv["amount_total"] == "12.345"  # exact decimal, as a string
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns WHERE table_name='billing_invoices' AND column_name='amount_total'"
                )
            ).scalar()
            == "numeric"
        )


def test_payments_and_adjustments_are_append_only(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, "platform_billing")
    new_owner(client, mailer, org_name="Ledger Firm")
    org = _org(admin, "Ledger Firm")
    inv = _invoice(admin, org["id"], "10")
    admin.post(f"/billing/invoices/{inv['id']}/payments", {"amount": "4", "method": "cash"})
    admin.post(f"/billing/invoices/{inv['id']}/adjustments", {"amount": "1", "reason": "Late fee per contract terms"})
    for table in ("billing_payments", "billing_adjustments"):
        for sql in (f"UPDATE {table} SET amount = 999", f"DELETE FROM {table}"):
            with pytest.raises(DBAPIError), owner_engine.begin() as conn:
                conn.execute(text(sql))


def test_billing_permissions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """Every billing mutation needs platform.billing.manage; roles that can only READ billing (readonly, admin)
    and roles with no billing access (support, auditor) are refused on every one of them."""
    admin = new_platform_admin(client, owner_engine, "platform_billing")
    new_owner(client, mailer, org_name="Perm Billing Firm")
    org = _org(admin, "Perm Billing Firm")
    inv = _invoice(admin, org["id"], "10")
    draft = _invoice(admin, org["id"], "10", issue=False)
    paid = _invoice(admin, org["id"], "10")
    admin.post(f"/billing/invoices/{paid['id']}/payments", {"amount": "4", "method": "cash"})
    i = f"/billing/invoices/{inv['id']}"
    mutations = [
        ("post", "/billing/invoices", {"organization_id": org["id"], "amount_total": "1", "currency": "OMR"}),
        ("post", f"/billing/invoices/{draft['id']}/issue", None),
        ("post", f"{i}/void", {"reason": "Trying to void without permission"}),
        ("post", f"{i}/payments", {"amount": "1", "method": "cash"}),
        (
            "post",
            f"/billing/invoices/{paid['id']}/refunds",
            {"amount": "1", "method": "cash", "note": "Trying a refund without permission"},
        ),
        ("post", f"{i}/adjustments", {"amount": "-1", "reason": "Trying an adjustment without permission"}),
    ]
    for role, can_read in (
        ("platform_support", False),
        ("platform_auditor", False),
        ("platform_readonly", True),
        ("platform_admin", True),
    ):
        other = new_platform_admin(client, owner_engine, role)
        assert (other.get("/billing/invoices").status_code == 200) is can_read, role
        for method, path, body in mutations:
            r = getattr(other, method)(path, body) if body is not None else getattr(other, method)(path)
            assert r.status_code == 403, (role, path, r.status_code)
    # nothing above changed any state
    assert admin.get(i).json()["invoice"]["outstanding"] == "10.000"
    assert admin.get(f"/billing/invoices/{draft['id']}").json()["invoice"]["status"] == "draft"
    owner = new_platform_admin(client, owner_engine, "platform_owner")
    assert owner.post(f"{i}/payments", {"amount": "1", "method": "cash"}).status_code == 201
    with owner_engine.connect() as conn:
        actions = {
            r[0]
            for r in conn.execute(
                text("SELECT action FROM platform_audit_events WHERE target_id = :i"), {"i": inv["id"]}
            )
        }
    assert {"payment.recorded"} <= actions
