# Workspace Builder (Control Center)

Location: **My Companies → company → Configuration → Open the workspace builder** (`/companies/{id}/workspace`), plus **Workspace Templates** in the main navigation (`/workspace-templates`). Company creation has an optional **Workspace** step (blank, template, or copy of another company’s configuration).

## Tabs

| Tab | What it does | Backend |
|---|---|---|
| Structure | tree (expand/collapse, keyboard operable), add section / folder / subfolder, rename, display name, description, active flag, **Move…** (valid destinations only), **Move up / down**, duplicate (structure only), archive / restore (with impact), delete (refused server-side when used), live **Windows folder preview**, per-item “what uses this item” | `nodes*` |
| Categories | category → Artify document kind (required), accounting domain/processing shown, reference-only switch, default folder, file types, active, archive/restore | `categories*` |
| Custom fields | 10 field types, options, simple validation, scope to categories, required/active, archive/restore | `fields*` |
| Naming rules | pattern editor with token list, **live preview with sample values**, one rule per category | `naming-rules*` |
| Filing rules | condition builder, target folder/category, raise/lower priority, **rule tester**, warnings about shadowed or equal-priority rules | `filing-rules*` |
| Permissions | open/restricted mode, grants table, add/remove grant, “check what a member can do” (owners/administrators only) | `permissions*`, `settings` |
| Templates | gallery (Artify starters + your organization’s), preview + plan-limit check, apply (add / replace with confirmation), copy from another company, save as template | `templates/apply`, `copy-from`, `workspace-templates` |
| History | numbered versions with actor and time, details, **restore preview then restore** | `revisions*` |

A strip above the tabs shows current usage against the plan’s limits for this company.

## Interaction rules implemented

- Every control calls a real endpoint; success shows a toast, failure shows the server’s plain-language message inside the dialog.
- Every change sends the loaded `base_version`; if someone else changed the workspace the page reloads and says so.
- Destructive actions use confirmation dialogs: archive shows impact (subfolders, categories, filing rules that will be switched off, documents), delete explains the refusal rule and offers archive, replace-apply needs an explicit tick, restore previews its effect first.
- Buttons are disabled (with the reason in the tab) when the company is inactive/archived or the member’s role/grants do not allow the action; the server enforces the same rules independently.
- Empty states explain what to do next. Nothing is created for the user automatically.
- Light and dark themes, the existing design tokens and UI kit; axe-core WCAG 2.0–2.2 A/AA scans pass on the builder in both themes in the browser test.

## Known limits of the UI

No drag-and-drop (use Move…, Move up/down); no Mobile preview tab (the Windows preview and the `upload-config` contract exist); no pending-change/batch “Apply” workflow (each change saves immediately, with version checking); English only (ADR-040 applies); no moderated usability sessions were run; field validation editing exposes only the common settings (length, min/max, decimal places) even though the API accepts date ranges, masks and allowed currencies.
