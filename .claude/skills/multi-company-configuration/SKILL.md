---
name: multi-company-configuration
description: Use when copying, templating, defaulting or sharing configuration between companies or organizations in the Artify repo (workspace templates, copy-from-company, company onboarding presets, any per-company setting). Enforces independence, provenance-vs-dependency and the two-company test fixture pattern.
---

# Multi-company configuration

## Principles
- **Every company owns its configuration.** Company settings (currency, financial year, VAT…) and workspace configuration are never inherited from, written back to, or shared with the organization or another company.
- **Copy, don't link.** Copying creates new ids immediately and the copy is independent. Keep at most a provenance note (`applied_template_id/_version`); no runtime query may join from a company row back to its source.
- **Configuration only.** Copying/exporting never includes documents, users, memberships, assignments, permission grants, money or personal data (`workspace_templates.export_definition` is the reference implementation).
- **Authorization on both ends.** Copying from company X needs `workspace.read` on X and reach to X (an unreachable source answers 404), plus `configure` on the target.
- **Never overwrite silently.** Applying into a non-empty company adds with collision suffixes; `replace` needs explicit confirmation and archives.
- **New defaults are templates**, not code paths. A company that never chose one must still work.

## Test fixture pattern
Create one owner and **two companies** (`make_company`), configure A and B differently, then assert: (1) changing A does not change B; (2) objects of B are 404/422 when referenced from A (parents, moves, category/rule targets, field scopes); (3) a member assigned only to A cannot read B; (4) an unrelated organization sees neither. See `test_p4_workspace.py::test_company_and_tenant_isolation`.
