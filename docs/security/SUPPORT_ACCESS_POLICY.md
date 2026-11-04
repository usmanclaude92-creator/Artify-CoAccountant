# Support Access Policy

How Artify staff may look at a customer organization, and how the system enforces it. Implemented in Phase 2; tests in `services/api/tests/test_p2_support.py` and the Playwright journey "support access needs the customer's approval".

## 1. Rules

1. **Default: no access.** Platform administrators see customer metadata and aggregates only (names, status, counts, plan, usage). They cannot read members, audit logs, documents or ledgers.
2. **Access is requested, never assumed.** A support administrator (`platform.support.request`) names one organization, a reason (≥ 20 characters, shown to the customer), a level and a duration (1–72 hours by default; the cap is the platform setting `support.grant_max_hours`).
3. **The customer decides.** Only the organization's **firm owner** (`support_grants.manage`) can approve, deny or revoke. They may approve a shorter duration than requested, never a longer one or a broader level.
4. **Time-limited and automatic.** The window starts at approval and ends at `expires_at`. Expiry is evaluated by the database inside the access policy; no background job has to run for access to stop. Unanswered requests become invalid after 14 days.
5. **Revocable at any moment** by the customer or by Artify (the requester, or `platform.support.revoke`), with a reason.
6. **Read-only.** The grant mechanism cannot write customer data: the platform role has no write policy on tenant tables.
7. **No impersonation.** Administrators never sign in as a customer user, never receive a customer session, and cannot act on behalf of one. There is no hidden or "break glass" path.
8. **Only the grant holder can use it**, only for their organization, only while it is active.
9. **Every use is recorded twice**: in Artify's platform audit and in the **customer's own audit log** (`audit_events.support_grant_id` set, actor type `platform_admin`), so the customer can see what was looked at and when.

## 2. Levels

| Level | Phase 2 exposure | Notes |
|---|---|---|
| `metadata` | the organization's member list (name, e-mail, role, status) and its audit log | the only level that unlocks anything today |
| `read` ("sensitive") | **identical to `metadata` in Phase 2** — no documents or ledgers exist yet | requires the customer's separate, explicit confirmation (`confirm_sensitive`); reserved for document access when those features exist. It will gain its own policies then, and must again require this explicit step |

## 3. Lifecycle

```
requested ──approve──▶ active ──(expires_at passes)──▶ expired
    │                    │
    ├──deny──▶ denied    └──revoke (customer or Artify)──▶ revoked
    └──withdraw (requester) / 14 days──▶ revoked / expired
```

Roles involved: requester = `platform_support` (or `platform_admin`/owner who holds `platform.support.request`); user of the grant = holder of `platform.support.view_data` (support, owner); decider = customer firm owner; revoker = customer firm owner, the requester, or `platform.support.revoke`.

## 4. What is checked, where

| Check | Layer | Test |
|---|---|---|
| Customer approval before any read | API (`_usable`) **and** RLS (`app.platform_granted_org_ids()` returns only active, unexpired grants of the calling administrator) | lifecycle test; RLS test queries the database as the platform role |
| Other organizations stay invisible while one is granted | RLS | RLS test (`users`, `organization_memberships` restricted to the granted organization) |
| A colleague cannot use someone else's grant | API, audited as `support.access_denied` | holder-only test |
| Expired / revoked grants stop working at once | RLS + API | expiry set in the past → 0 rows and 403 |
| Only the firm owner decides; other roles and other tenants cannot | tenant permission `support_grants.manage`; RLS `tenant_select/decide` | authorization-rules test (accountant 403; other firm 404) |
| Cannot approve more than requested, or sensitive access that was not requested | API | same |
| Sensitive level needs explicit confirmation | API | sensitive-level test |
| Suspended requester loses access | session revocation | requester-gone test |
| Tenant business tables remain closed even with a grant | database privileges (`companies`, `company_memberships`, `invitations`, `devices` → permission denied) | RLS test |

## 5. Notifications

The organization owner receives an e-mail ("Artify support is requesting access…") with the reason and a link to **Support access** (`/support-access` in the customer console), where they approve, deny or end access. Mail is best-effort (ADR-026) and no real provider is configured, so in development it is written to the API log; the request is also visible in the console without the e-mail.

## 6. Known limitations

- Approval authority is the **firm owner only**; a `practice_admin` cannot approve. There is no delegate.
- A **suspended** organization is read-only for its own users (Phase 1), so its owner cannot approve or revoke through the UI until it is reactivated; Artify can still revoke.
- Grants are organization-wide. Company-scoped grants (the column exists) and per-section access arrive with documents.
- No automatic reminder before a grant expires; no "extend" action (request again).
- There is no customer-side digest of support activity beyond the audit log.
- The 72-hour and 14-day figures are defaults chosen by the implementer, not owner-approved policy; the cap is adjustable in Platform Settings.
