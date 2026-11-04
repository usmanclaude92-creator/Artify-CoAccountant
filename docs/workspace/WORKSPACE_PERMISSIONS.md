# Workspace Permissions

Two stages. Stage 1 is the existing Phase 3 policy (role ∩ company scope); stage 2 is the workspace allow/deny engine. Backend authorization is mandatory on every route (the route table test requires `Requires`/`Public`).

## Stage 1 – role keys (catalog in `permissions.py`)

| Key | Scope | Held by |
|---|---|---|
| `workspace.read` | company | owner, administrator, practice manager, accountant, assistant accountant, staff, document operator, read-only |
| `workspace.manage` | company | owner, administrator, practice manager |
| `workspace.permissions.manage` | company | owner, administrator |
| `workspace_templates.manage` | organization | owner, administrator, practice manager |

Company roles (client side) hold none of them in Phase 4; Mobile access arrives with the Mobile phase. Members below manager reach only the companies they are assigned to (Phase 3 rule, unchanged).

## Stage 2 – node actions

`view`, `upload`, `edit` (create inside / edit details), `rename`, `move`, `archive` (archive and restore), `delete`, `configure` (company level: sections, categories, fields, rules, templates, history), `manage_permissions`.

Role defaults: `workspace.read` → `view`; `workspace.manage` → `upload, edit, rename, move, archive, delete, configure`; `workspace.permissions.manage` → `manage_permissions`.

Explicit grants (`workspace_permissions`): allow/deny × (user | organization role) × (node | whole workspace) × action. Decision, in order (`decide`):

1. Holders of `workspace.permissions.manage` are **always allowed** (an owner/administrator can never be locked out; grants cannot target these roles — 422).
2. `read_only` members are capped at `view` (also enforced when granting).
3. Walk **nearest node → ancestors → company level**; the first level that holds a grant for this action decides; **deny beats allow on the same level**. Grants therefore inherit **downwards only**, never up to a parent or sideways.
4. No grant: the role default applies in `open` mode. In `restricted` mode, members without `workspace.manage` get **nothing** unless explicitly allowed.

Subjects must be active members of the organization (user) or a known organization role. Grants for members not assigned to the company have no effect because stage 1 already answers 404.

## Hidden means hidden

A node a caller may not `view` is omitted from `GET /workspace`, `upload-config`, categories (placed in a hidden folder), and answers **404** on direct access (also as a parent in create/move). A visible-but-forbidden action answers **403**. Configuration lists (naming and filing rules, history, grants) require `configure`/`manage_permissions` and are otherwise empty or 403, so hidden folder names cannot leak through them. A child shown without its hidden parent is re-rooted in the response.

## Endpoints

`GET/POST …/workspace/permissions`, `DELETE …/permissions/{id}`, `POST …/permissions/check` (explain another member’s effective permission), `GET …/workspace/access/me`, `PATCH …/workspace/settings` (access mode). Every change creates a revision and an audit event. Grants and the access mode are deliberately **not restored** by history restore.

## Not built

Per-document/row-level RLS mirroring of section grants (documents do not exist yet; Phase 6 must add it), custom organization-defined roles, grants for client-side (company role) users.
