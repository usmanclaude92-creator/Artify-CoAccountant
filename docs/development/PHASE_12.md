# Phase 12 — Cloud Synchronization, Windows Integration, Security Testing and Commercial Release

**Status: NOT STARTED.** Requires decisions D-3 (production hosting/residency), D-4 (tenant keys), D-8 (retention values), and D-11 (legal) before any external pilot.

## Objectives

Harden synchronization to production grade, finish Windows integration, prove security and recoverability, and prepare the commercial release.

## Functional scope

- Sync hardening: conflict resolution UX end to end, large-Company performance (snapshot resync, partial mirrors, on-demand placeholders), retention/purge with tombstones, device revocation and wipe flows, fuzzy edge cases (clock skew, partial uploads, renamed trees).
- Windows integration: installer polish, auto-update channels, shell integration decisions, AV/OneDrive compatibility fixes, long-path and Unicode matrix, multi-user machines.
- Security: external penetration test, remediation, audit-chain hardening (hash chaining/anchoring), key management (D-4 outcome), SBOM, dependency posture, secrets rotation drill, incident response runbook, privacy review.
- Reliability: backup/PITR and DR drills with measured RPO/RTO, load/soak tests, observability dashboards and alerts, status page, rate-limit tuning.
- Commercial: production infrastructure, environment promotion, release process, support runbooks and Super Admin support flows, billing integration per D-2 if not earlier, terms/DPA/sub-processor documentation (legal-owned), onboarding documentation.
- Data export and account closure flows; erasure workflow.

## Technical implementation

Infrastructure as code for production (`infrastructure/deployment`), blue/green or rolling deploys with migration discipline (expand/contract), per-tenant restore/export tooling, synthetic monitoring, chaos tests on workers/providers, security regression suite promoted to a required gate.

## Database changes

Retention/purge job roles and tables, audit hash-chain columns, `sync_conflicts` resolution fields, partition management, any tenant-key reference tables (D-4), indexes tuned from production-like load tests.

## API requirements

Data export/closure endpoints, device wipe signaling, admin retention controls, rate-limit headers, deprecation policy and `/api/v1` stability commitments, status/health endpoints.

## Dependencies

All previous phases; D-3, D-4, D-8, D-11.

## Security requirements

All sections of `SECURITY_ARCHITECTURE.md` verified by evidence; pen-test critical/high findings fixed before release; restore drills with RLS re-verification; no production credentials in any non-production environment.

## Testing requirements

Executable: all TS-* scenarios (full regression), TS-SYNC-01..07, TS-SEC-01..06, performance/soak, DR drills with recorded timings, upgrade/migration rehearsals on production-sized synthetic data, accessibility regression.

## Acceptance criteria

1. Third-party penetration test completed; no unresolved critical/high issues.
2. Measured (not asserted) RPO/RTO from drills are reported against targets.
3. Full regression of isolation, workspace, accounting, Excel, entitlement and sync scenarios passes in a production-like environment.
4. Pilot-ready: legal documents in place, support runbooks exercised, rollback rehearsed.

## Completion checklist

- [ ] Pen-test report and remediation log
- [ ] DR drill report
- [ ] Release checklist signed off by owner
- [ ] `PHASE_12_COMPLETION_REPORT.md`

## Known risks

Late discovery of isolation or sync flaws; third-party test findings consuming schedule; hosting/residency constraints; store review delays; operational maturity (on-call, runbooks) lagging the code.
