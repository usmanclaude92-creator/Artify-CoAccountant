# CLAUDE.md — Artify Co-Accountant

Guidance for Claude Code working in this repository. Keep this file short; the canonical detail is in `docs/`.

## Status
Phase 0 approved. Phase 1 (SaaS foundation) delivered for review with a few open items (`docs/development/PHASE_1_COMPLETION_REPORT.md`). Phase 2 (Super Admin) delivered for review (`docs/development/PHASE_2_COMPLETION_REPORT.md`). **Do not start Phase 3 until the owner approves Phase 2.** Phase scopes are in `docs/development/PHASE_N.md`. Project skills in `.claude/skills/` (6 of 17) describe the Phase 1 code; the Super Admin has no skill yet.

## Commands
`make check` (everything except browser e2e), `make test-api`, `make db-reset`, `make client` (regenerate OpenAPI + TS client after API changes), `make e2e`, `make boundaries`, `make audit`, `make compose-smoke`, `make hooks`.

## Read first
`docs/architecture/GLOSSARY.md` (terms) → `docs/architecture/DECISIONS.md` (ADRs, open decisions) → the architecture doc for the area you touch.

## Non-negotiable invariants
1. **Tenant = Organization.** Company-owned rows carry `organization_id` and `company_id` with a composite FK; RLS is enabled and forced; the app DB role has no `BYPASSRLS`.
2. Never trust client-supplied tenant/company/section ids; validate against memberships on the server. Out-of-scope objects return 404.
3. **No hard-coded categories, sections or folders** anywhere (processing, Mobile, Desktop, exports). Everything comes from the Company's workspace configuration; internal logic keys off Artify-controlled **document kinds** via the category mapping.
4. Workspace nodes are referenced by stable id/key, never by display name. Archive, don't delete, once referenced. Templates are copied, never linked.
5. Money: `Decimal` in code, `NUMERIC` in the database, strings in JSON. Never `float`.
6. **AI never approves or finalizes.** Approval transitions and entry approval require `actor_type = user` plus permission. AI output is stored as suggestions/candidates.
7. Never invent missing financial data: missing is `NULL` plus a flag.
8. Original documents and approved records are immutable; corrections are new versions/reversals.
9. Limits and quotas are enforced only by the backend `EntitlementService`; no limit constants in code.
10. Platform admins have no default access to customer business data; exceptions use audited, time-limited support grants.
11. No production credentials or real customer documents in the repo, tests or CI. Synthetic data only.

## Working rules
- Report honestly: never claim a feature is implemented because it is documented, never claim a test passed unless it was executed.
- Update the relevant docs and `DECISIONS.md` in the same change as the code.
- Run `python3 tools/check_docs.py` after editing docs.
- Project skills are created in the phase listed in `docs/development/PROJECT_SKILLS_PLAN.md`; do not add unverified third-party skills.
