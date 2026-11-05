# Backend audit for the mobile app

Verified against the running code and the generated OpenAPI contract, not against documentation.

| Need | Endpoint(s) | Verdict |
| --- | --- | --- |
| Sign in, second factor | `POST /auth/login` → `{status, tokens | mfa_token}`; `POST /auth/mfa/verify` | **Ready.** Enrolment (`/auth/mfa/enroll`) exists but the app sends people to the web/Windows app for it. |
| Session refresh and revocation | `POST /auth/refresh`; `POST /auth/logout` (204); sessions list/revoke | **Ready.** |
| Password reset | `POST /auth/password-reset/request` and `/confirm` (token from the email) | **Ready.** The email link targets the web; the app accepts the code by hand. |
| Who am I, memberships | `GET /me` (memberships with organization role and company roles) | **Ready.** |
| Companies of an organization | `GET /orgs/{org}/companies` (paged, RLS-scoped) | **Ready.** |
| Device registration and limits | `POST /devices` (`android`/`ios` already allowed), `GET /devices`; plan limit → `402 entitlement_exceeded` | **Ready.** Revoking the device ends its session. |
| Workspace for uploading | `GET /companies/{id}/workspace/upload-config` (targets, categories, fields, `version`, **ETag/304**) | **Ready, with one gap fixed** (below). |
| Upload | `POST …/documents/uploads` (idempotent by key) → `PUT …/{op}/content` (raw body) → `POST …/{op}/complete` | **Ready.** Whole-file PUT: there is no byte-range resume for uploads, only operation-level idempotency. |
| History | `GET …/documents` (paged, `created_by`, `q`), `GET …/documents/{id}`, `…/content`, `…/versions`, `PATCH` rename | **Ready.** |
| Processing status | `GET …/documents/{id}/intelligence` | **Ready** (404 when nothing was processed). |
| Notification feed | `GET …/documents/events` | Exists; there is **no per-user notification inbox** and **no push-token endpoint**. |
| Entitlements as data | enforced server-side (`402`); no client-readable plan document | The app shows the server's refusal; it never reads or stores limits. |

## The one backend extension
`upload-config` did not carry what a phone needs to render a form: field `description`, `default_value`, `validation` and `reference_target`, and node/category `description`. They were added (additive, ETag-compatible, nothing removed or renamed) and covered by `tests/test_p11_mobile_contract.py`.

## Not available (and therefore not built)
* Push delivery (no provider chosen, no token endpoint).
* Resumable byte-range uploads.
* Conditional field visibility (the backend has no such rule).
* A readable subscription/entitlement document for the client.
* Company logos or "last activity" on company records.
