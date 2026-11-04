# SaaS Tenant Model

Canonical definitions: [`GLOSSARY.md`](GLOSSARY.md). Implementation of isolation: [`../database/ISOLATION_AND_RLS.md`](../database/ISOLATION_AND_RLS.md).

## 1. Hierarchy

```
ARTIFY PLATFORM                              (not a tenant; operator)
 ├─ Platform Admins                          (separate auth realm; platform_admins)
 │
 ├─ Organization  [type = accounting_firm]            ← THE TENANT; owns the subscription
 │    ├─ Organization members: firm_owner, practice_admin, accountant, staff
 │    ├─ Workspace templates, Excel template presets, custom roles (organization-owned libraries)
 │    ├─ Company A ── company boundary
 │    │     ├─ Company members: company_owner, company_employee
 │    │     ├─ Accountant assignments
 │    │     ├─ Workspace configuration (sections / folders / categories / fields / workflow)
 │    │     ├─ Documents, financial records, accounting entries
 │    │     └─ Excel templates, report configurations, chart of accounts, VAT setup
 │    ├─ Company B ── (completely independent configuration and data)
 │    └─ Company C …
 │
 └─ Organization  [type = independent_accountant]     ← same model; usually one member
      └─ Company X, Company Y …
```

Rules:

1. **The Organization is the tenant** (ADR-001). Every tenant-owned row carries `organization_id`.
2. **A Company belongs to exactly one Organization** and is a protected data boundary inside it. Every company-owned row carries `organization_id` **and** `company_id`, tied by a composite foreign key (ADR-002).
3. **Users are global identities; access comes only from memberships.** A person can be an accountant in two Organizations (two `organization_memberships`) or a client employee of a Company in one Organization. Each request is evaluated against one *active* Organization context chosen by the client and validated server-side.
4. **Independent accountants** subscribe directly, creating an Organization with `type = independent_accountant`. They use the same Control Center, Desktop and APIs. If they hire staff, they change plan/type; no data moves.

## 2. Who can see what

| Principal | Organization-level data (staff list, plans usage, org libraries) | Company A data | Company B data |
|---|---|---|---|
| `firm_owner`, `practice_admin` | Yes (admin) | Yes, all Companies of the Organization | Yes |
| `accountant`, `staff` | Limited (own profile, assigned work) | Only if **assigned** (`accountant_assignments`) | Only if assigned |
| `company_owner` | No | Only their own Company | No |
| `company_employee` | No | Own Company, permissions limited by role and section permissions (default: upload and view own submissions) | No |
| Platform Admin | Tenant metadata and metering only | **No**, unless an active support access grant | **No**, unless grant |
| User of another Organization | No | No | No |

Access to *a Company* is necessary but not sufficient: **section-level permissions** (`workspace_section_permissions`) can further restrict who can view, upload, review, approve or export within a Company (e.g. payroll section limited to two named accountants).

## 3. How users and accountants are assigned to companies

- **Company members** (`company_memberships`): client-side users are invited into a Company by a Company-owner or an authorized Organization member through `invitations`. Role: `company_owner` or `company_employee` (or a custom company-scoped role).
- **Accountant assignments** (`accountant_assignments`): an Organization admin assigns `accountant`/`staff` members to Companies with an `assignment_type` of `primary`, `secondary`, or `reviewer`. Many accountants per Company and many Companies per accountant are supported. Assignment changes take effect on the next request (membership/assignment data is re-resolved server-side with a short-lived cache invalidated on change) and are audited.
- **Effective access** = role permissions ∩ company scope (all Companies for owner/admin, assigned set for accountants, own Company for client users) ∩ section permissions ∩ entitlement checks.

## 4. Subscription binding

- `subscriptions` belong to an Organization and reference a `subscription_plans` row. Effective limits = plan entitlements + per-subscription overrides (see [`SUBSCRIPTION_AND_ENTITLEMENTS.md`](SUBSCRIPTION_AND_ENTITLEMENTS.md)).
- Limits are counted across the whole Organization (e.g. `limit.companies`) or per Company (e.g. `limit.workspace_sections_per_company`), as defined in the entitlement catalog.
- Suspension (by Platform Admin, or automatically for non-payment after a grace period) moves the Organization to a restricted state: reads and exports of existing data remain available (to avoid holding customer data hostage), while uploads, new Companies and AI processing are blocked. The exact policy is configured, not hard-coded.

## 5. Enforcement layers (summary)

| Layer | Mechanism |
|---|---|
| 1. Authentication | Realm-specific tokens; sessions bound to device |
| 2. Context resolution | Server resolves memberships for the user; client-supplied `organization_id`/`company_id` are validated against them, never trusted |
| 3. Authorization | Central policy engine: permission + scope + section rule + entitlement |
| 4. Data constraints | Composite FKs ensure a child row cannot point at another Company's parent row |
| 5. Row-Level Security | Database rejects reads/writes outside the transaction's tenant/company context |
| 6. Storage | Opaque, tenant-prefixed object keys; short-lived signed URLs issued only after step 3 |
| 7. Audit | Access and changes recorded in append-only audit logs |

## 6. Platform-level support access

1. A Platform Admin requests access (reason, scope: Organization metadata / specific Company, read-only, duration ≤ configured maximum).
2. By default the tenant `firm_owner` approves (break-glass for security incidents: two Platform Admins approve, tenant is notified).
3. An active grant lets RLS and the policy engine admit that admin to the named scope only.
4. Every read is audited to both the platform and the tenant audit logs; the tenant can see it. Grants expire automatically and can be revoked by the tenant at any time.

## 7. Scenarios this model must satisfy

These are specified as testable scenarios in [`../development/TEST_SCENARIOS.md`](../development/TEST_SCENARIOS.md): cross-tenant denial (TS-ISO-*), cross-company denial inside one firm (TS-ISO-*), accountant sees only assigned Companies (TS-ISO-*), client employee confined to own Company (TS-ISO-*), Platform Admin cannot read documents without grant (TS-ISO-*).
