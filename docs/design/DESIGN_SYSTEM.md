# Design System and Interface Principles

Documentation only in Phase 0; no UI exists. The system is implemented as `packages/ui` in Phase 3 (Control Center) and reused by Super Admin (Phase 2 consumes an initial subset — see roadmap) and Desktop.

## 1. Character

Premium enterprise accounting software: calm, precise, trustworthy. Dense where accountants need density (tables, review screens), generous where non-technical users configure (workspace builder).

**Avoid:** generic SaaS landing templates, decorative gradients and heavy animation, decorative card-grids, fabricated statistics, testimonials, customer logos, fake integrations, and any control that does nothing. Empty states say what is empty and what to do; they never display invented numbers.

## 2. Shared brand, different priorities

| Surface | Priority |
|---|---|
| Super Admin | Operational clarity, tables, filters, audit, high information density, safety confirmations on destructive actions |
| Control Center | Configuration clarity, guided builders (workspace, templates, mappings), dashboards of real data |
| Desktop | Throughput: keyboard-first review, side-by-side source and data, bulk actions, minimal chrome |
| Mobile | Capture speed, large touch targets, clear progress/sync status, minimal typing |

## 3. Foundations

- **Tokens first.** Color, spacing, radius, elevation, typography, motion are CSS variables (web/desktop) and a generated Dart theme (mobile) from one token source. Light and dark themes are token sets: `:root` defaults, a `[data-theme=dark]` override, and a system-preference default. No hard-coded colors in components.
- **Typography:** one UI sans family plus a tabular-figures numeric style for all financial columns; consistent type scale; amounts right-aligned with fixed decimals per currency; never rely on color alone for debit/credit or status.
- **Color roles:** neutral surfaces, one brand accent, semantic status colors (success/warning/danger/info) tested for contrast in both themes.
- **Density modes:** comfortable and compact (tables).
- **Internationalization:** all strings from catalogs; RTL-ready layout using logical properties (`margin-inline-start`, etc.); Arabic and English (decision D-9); locale-aware number/date formatting with Gregorian default and optional Hijri display.
- **Accessibility:** WCAG 2.2 AA target; full keyboard operation including the tree editor and drag-and-drop alternatives; visible focus; ARIA via vetted primitives; reduced-motion support; error text associated with fields; screen-reader-friendly live regions for async status.
- **Responsive:** web layouts function at phone width (administration read/approve), optimized for ≥1280px working use.
- **Motion:** functional only (state changes, drag feedback); ≤200ms; respects reduced-motion.

## 4. Component inventory (planned)

App shell (nav, org/company switcher, breadcrumbs), data table (sorting, filtering, column config, virtualization, bulk actions), form kit with validation messages, tree editor (workspace builder), dialog/drawer/popover, toast/inline alerts, status chips (workflow stages use customer-named stages with semantic stage-type color), amount/currency display and input (Decimal-safe), file viewer (PDF/image with bounding-box overlay for extraction review), diff view (change-set/template apply preview), stepper/wizard, empty/error/loading states, entitlement banner (limit reached with actionable message).

## 5. Key interaction patterns

1. **Destructive and financial actions** need explicit confirmation naming the object and impact (counts) — never a generic "Are you sure?".
2. **Draft → review → apply** for configuration changes (workspace change sets, template apply, mapping changes) with impact analysis.
3. **Provenance visible:** values show source (extracted/edited/AI-suggested) and who/when on hover or in a details panel; AI suggestions are visually distinct and always require explicit acceptance.
4. **Limits are visible, not surprising:** usage meters near the relevant creation actions; blocked actions explain the limit and who can change it.
5. **Consistent status language** for workflow, sync, processing and subscription states across all surfaces.

## 6. Workspace Structure Builder (priority UX)

See [`WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md`](../architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md#12-workspace-structure-builder-control-center-ux-requirements): tree editor with inline rename, drag-and-drop plus keyboard/button alternatives, side panel, three live previews (Control Center, Mobile, Windows folders), pending change summary, impact analysis, template gallery, history. Usability-tested with non-technical accountants before Phase 4 sign-off (moderated sessions with at least five participants; task success and time-on-task recorded).

## 7. Governance

Design tokens and components are versioned; visual regression tests (Phase 3 onward); a11y checks in CI (axe) plus manual keyboard/screen-reader review per release; the `frontend-design` project skill (see `PROJECT_SKILLS_PLAN.md`) encodes these rules for the coding agent.
