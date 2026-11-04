# Application Architecture

Describes each application's responsibilities, boundaries, and how they retrieve configuration. Visual design lives in [`../design/DESIGN_SYSTEM.md`](../design/DESIGN_SYSTEM.md).

## 1. Artify Super Admin vs. Firm Control Center

These are the most commonly confused surfaces; they are **different products on the same codebase**.

| Aspect | Artify Super Admin | Firm Control Center |
|---|---|---|
| Users | Artify staff (`platform_admins`) | Organization members (firm owner, practice admin, accountant, staff) |
| Auth realm | **Platform** realm: separate token audience, hostname/cookie scope, mandatory MFA, shorter sessions, optional IP allow-list | **Tenant** realm |
| API surface | `/api/v1/platform/*` | `/api/v1/orgs/*`, `/api/v1/companies/{company_id}/*` |
| Database role | `artify_platform` role — policies grant platform metadata and aggregate metering | `artify_app` role with per-request tenant/company RLS context |
| Sees | Organizations (metadata), plans, subscriptions, payments, usage aggregates, platform health, platform audit | Its own Organization's Companies, people, configuration, usage, audit |
| Cannot see (by default) | Any document, extracted value, accounting entry, Excel file, workspace content of a tenant | Anything outside its Organization; other Companies if not assigned |
| Exceptional access | Only through `support_access_grants` (time-limited, scoped, tenant-visible, audited) | n/a |
| Navigation | Tenants, Plans, Subscriptions & Billing, Usage & Storage, Processing volumes, Platform Health, Configuration, Support Access, Security & Audit | Overview, Clients, Team, Assignments, Roles, Workspace, Processing Monitor, Accounting Config, Templates, Reports, VAT/Tax, Subscription, Notifications, Audit, Devices |
| Code | `apps/web` route group `(platform)` | `apps/web` route group `(console)` |
| Deployment | Own hostname/surface build (`APP_SURFACE=platform`) | Own hostname/surface build (`APP_SURFACE=console`) |

Security does not depend on route separation: the API enforces realm, role and scope on every request (ADR-013, ADR-014).

### Super Admin responsibilities (Phase 2)
Manage Organizations (firms and independents); create/edit plans and entitlements (no code deploy); manage subscriptions and billing records; monitor usage, storage and processing volumes; manage per-subscription overrides; monitor platform health; manage platform settings; manage support access; review platform audit/security events; suspend/reactivate Organizations when authorized (with reason, audited, dual-control for destructive actions).

## 2. Firm Control Center (Phases 3–4, extended by 7–9)

An **administration and configuration** environment. It does not reproduce the Desktop bookkeeping workspace.

Modules (delivery phase in the roadmap matrix):

1. Overview Dashboard — real data only (document counts by workflow stage, items awaiting review, sync/device health, usage vs limits).
2. Client Company Management.
3. Accountant and Staff Management.
4. Client-to-Accountant Assignments.
5. User Roles and Permissions (system roles; custom roles).
6. **Workspace Structure & Customization** (see [`WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md`](WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md)).
7. Document Processing Monitor.
8. Company Accounting Configuration (chart of accounts, account role mappings, periods, currencies).
9. Excel Template Management.
10. Report Configuration.
11. VAT and Tax Configuration.
12. Subscription and Usage Management.
13. Notifications.
14. Audit Logs and Security.
15. Device and Application Management.

## 3. Artify Co-Accountant Desktop (Phases 5–10, 12)

Tauri (Rust shell) + React + TypeScript. The operational workspace for accountants and authorized client-side users.

- **Shell responsibilities (Rust/Tauri):** secure token storage in the OS credential vault (Windows Credential Manager/DPAPI), file-system watcher, local mirror management, upload/download queue, SQLite cache, auto-update, device registration. The Rust side exposes a *narrow* command surface to the UI (no arbitrary file-system or shell access; Tauri capability allow-list).
- **UI responsibilities (React):** document workspace, review UI, validation, classification, entries, Excel export, daily close.
- **Local data:** SQLite cache of workspace configuration versions, document metadata, queue state; documents cached as files. Cache is encrypted at the file level where the OS supports it and the session key is released only after login; encryption of the SQLite file is an implementation choice finalized in Phase 5 (SQLCipher vs OS-level).
- **Offline:** capture, upload queue, local viewing of cached documents and review drafts. Approvals and exports require connectivity for permission and period checks (decision D-10 covers Mobile; Desktop offline approval is intentionally not allowed).
- **Role-based UI:** one binary; accountants see processing/accounting modules; client-side users see submission and status views. The server decides capabilities; the UI merely reflects them.

## 4. Artify Co-Accountant Mobile (Phase 11)

Flutter, Android + iOS. Primary purpose: document **collection and submission**.

- Login → pick Company (only Companies the user may upload for) → scan/capture → pick **category and folder from that Company's own configuration** → upload.
- Multi-page scanning into one PDF/document; edge detection, perspective correction, enhancement (on-device).
- **No hard-coded categories.** The app calls `GET /companies/{id}/workspace/upload-config` and renders whatever sections, folders, categories and required fields come back for the user's permissions. The response carries `workspace_version`; the app caches it and revalidates with `If-None-Match`.
- Upload queue with progress, retries, resumable multipart uploads and a sync status screen; notification inbox; document history (own submissions).
- Secure storage of tokens in Keychain/Keystore; biometric re-unlock is local-only convenience, not an authentication replacement.

## 5. How Desktop and Mobile retrieve and apply the workspace configuration

| Concern | Mechanism |
|---|---|
| Discovery | `GET /companies/{id}/workspace` (Desktop, full tree incl. archived-for-display) and `GET /companies/{id}/workspace/upload-config` (Mobile, upload-capable targets only) |
| Versioning | Both return `workspace_version` and an ETag; changes are delivered also through the change feed (`workspace.changed` records) |
| Permission trimming | Server filters by section permissions for the calling user; the client never sees sections it cannot use |
| Stale config | Every upload/file operation sends `workspace_version`; if the target was archived or changed, the server answers with a structured `409` the client can resolve; documents are never silently re-filed |
| Local folders | Desktop derives the Windows folder tree from the same configuration (see [`STORAGE_AND_SYNC_ARCHITECTURE.md`](STORAGE_AND_SYNC_ARCHITECTURE.md)) |

## 6. Shared packages

| Package | Purpose |
|---|---|
| `packages/ui` | React component library implementing the design system (web + Desktop) |
| `packages/types` | Shared TypeScript types not derivable from OpenAPI (UI view models, permission keys mirrored from the server catalog by a generated file) |
| `packages/api-client` | TypeScript client **generated** from the OpenAPI schema (never hand-edited) |
| Flutter client | Generated from the same OpenAPI schema into `apps/mobile` |

## 7. Cross-application consistency rules

- Permission keys, document kinds, stage types and entitlement keys are defined once on the server and exposed through the API/OpenAPI; clients do not invent their own copies.
- Money is a string in transit, never a JS `number`/Dart `double` for calculation; clients use decimal libraries for display arithmetic and defer authoritative calculation to the server.
- All user-facing strings go through i18n catalogs from the first screen (decision D-9: Arabic/RTL and English).
