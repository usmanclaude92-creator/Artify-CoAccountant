# Workspace Templates

## Sources

- **Artify system templates** (seeded by `artify-api seed` from `workspace_templates_data.py`; read-only for tenants): General Business, Trading, Construction, Professional Services, Manufacturing, Retail, Hospitality, Custom Blank Workspace. They are starting points, not defaults: no company receives one unless a user chooses it.
- **Organization templates**: created from a company (“save as template”) or from a supplied definition; versioned; archive-able. Quota: `limit.workspace_templates`.

A template version is an immutable JSON `TemplateDefinition`: sections/folders (recursive, stable local `ref`s), categories (with document kind and folder ref), custom fields (+options, scope refs), naming rules, filing rules. It is schema- and cross-reference-validated (`check()`), including node-name safety, duplicate sibling names, kind catalog, field definitions and filing conditions.

## Apply = deep copy

`POST /companies/{id}/workspace/templates/apply` creates **new ids** for every node, category, field, option, applicability row, naming rule and filing rule, preserving parent/child relationships; template-local refs are remapped (including `custom:<field>` conditions). The company stores only `applied_template_id/_version` as **provenance** — nothing reads it for behaviour.

- `mode: add` (default) keeps everything existing; colliding names get ` (2)`, keys get `-2`, priorities are offset after existing rules; a naming rule for a category that already has one is skipped and reported.
- `mode: replace` requires `confirm_replace: true` and first **archives** the current nodes, categories, fields, naming and filing rules (nothing deleted; documents would stay filed in archived nodes).
- `dry_run: true` returns counts and whether the result fits the plan limits (per limit: after, limit, fits) and changes nothing. A real apply that would exceed a limit fails with 402 and changes nothing.
- Requires company-level `configure`; suspended/archived companies are read-only.

## Independence (tested)

Applying to two companies yields disjoint ids; renaming/archiving in one leaves the other and the template unchanged; publishing a new version of an organization template leaves companies that used version 1 unchanged; applying version 1 again still works.

## Versioning and updates

`POST /orgs/{org}/workspace-templates/{id}/versions` adds an immutable version from a company or a definition. System templates change only by an Artify release (a changed definition becomes a new version on the next seed; old versions remain). **Not built:** the “review differences and merge” flow for companies that applied an older version (Phase 0 design §6); companies simply keep what they have, and a user can apply a newer version in `add` mode.

## Save as template / copy a company

Both use `export_definition`: configuration only. Permissions, users, documents and financial data are never exported. Filing rules that depend on company-specific category ids are skipped with a warning. `POST /companies/{id}/workspace/copy-from` applies another company’s exported configuration (caller needs `workspace.read` on the source and reach to it; an unreachable source answers 404). Company onboarding accepts `workspace: {mode: blank|template|copy, …}` and applies it inside the company-creation transaction (a failure rolls the whole creation back).
