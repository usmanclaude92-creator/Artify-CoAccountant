# Organization Management

An **Organization** is the tenant (a firm or an independent accountant). Companies, people and subscription hang off it. Organization creation is a Super Admin or sign-up action; the Control Center manages an existing one.

## Organization settings (`/settings`)

Stored on `organizations` (new columns in migration 0005): `contact_email`, `contact_phone`, `address_line`, `city`, `timezone`, `notification_preferences`. Existing: `legal_name`, `display_name`, `slug`, `type`, `country`, `default_currency`, `status`.

- Editable by roles holding `organization.settings.update` (owner, administrator). Everyone with `organization.read` can view.
- `legal_name`, `slug`, `type` and `country` are read-only here (managed by Artify through the Super Admin).
- `default_currency` is only a **default for new companies**. Each company keeps its own base currency; changing the organization default never changes a company.
- `timezone` is validated against the IANA database; unknown names are rejected (422).
- Notification preferences are validated against a fixed key list and **stored only**. The response carries `notifications_sent: false`; no notification is sent in Phase 3.
- E-mail delivery status is reported honestly (`email_delivery.configured` is true only when the SMTP backend is configured). Without it, invitations are created but nothing is e-mailed, and the UI says so.

Organization settings are separate from company settings: company profile and accounting settings live on `companies` and are edited on the company page with their own permissions (`companies.update`, `company_settings.manage`).

## Subscription and usage (`/subscription`, dashboard)

Read-only. Data comes from the backend `EntitlementService` and plan tables: plan, version, status, period, trial end, price (exact decimal string), and limits with **live** counts for enforceable keys (`limit.companies`, `limit.users`, `limit.accountants`, `limit.devices`). Other keys show *Not metered* or *Not enforced yet*; they are never shown as zero. A flag key shows *included* or *not included*. Attention messages cover past-due, suspended, trial ending and over-limit. Reaching a limit blocks creating more (HTTP 402 `entitlement_exceeded`); it never deletes or hides data. There is no upgrade, payment or invoice flow: the next step text only says to contact Artify support.

## Activity and audit (`/activity`)

The organization’s own `audit_events` (append-only, tenant-scoped by RLS) with filters for text, action, company and date range, and pagination. Platform-internal logs are not exposed. Artify support actions under an approved grant appear because they are mirrored into the customer’s log (ADR-029).

## Ownership

An organization always has at least one owner. The last owner cannot be demoted, suspended or removed through member updates; the approved path is an **ownership transfer** (`POST /orgs/{id}/ownership-transfer`): owner only, MFA-verified session, typing the organization identifier to confirm, target must be an active member. In one transaction the target becomes owner and the caller becomes administrator. See [`ROLE_AND_PERMISSION_MODEL.md`](ROLE_AND_PERMISSION_MODEL.md).
