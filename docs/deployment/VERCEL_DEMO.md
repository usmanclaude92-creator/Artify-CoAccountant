# Hosted demo on Vercel + Supabase (preview only)

A throw-away environment so the owner can click through the product. **It is not the production architecture** (docs/architecture/SYSTEM_ARCHITECTURE.md): production runs the container images with Redis, a worker, SMTP and an S3 store.

## Shape

| Part | Where | Notes |
|---|---|---|
| Web (Next.js, `apps/web`) | Vercel project `coa`, `coa.artifysols.com` | env `ARTIFY_API_URL` points at the API |
| API (FastAPI, `services/api`) | Vercel project `coa-api`, region `bom1` (next to the database), entry `services/api/api/index.py` | `vercel.json` routes every path to the ASGI function |
| Database | a dedicated Supabase project `artify-coa` (Mumbai) | reached through the Supavisor session pooler (IPv4); pool size 1 per identity (`ARTIFY_DB_POOL_SIZE`, `ARTIFY_DB_MAX_OVERFLOW`) |

## How the database is prepared

On the first request of a new instance `scripts/bootstrap_remote.py` runs (advisory-locked, idempotent, skipped once the schema carries the marker `seeded:<head>`): creates the NOLOGIN group roles and grants `CREATE` on `public` to `artify_definer` (a managed-Postgres owner is not a superuser and `ALTER … OWNER TO artify_definer` needs it), applies migrations 0001–latest, creates the three login roles with passwords from the environment, seeds reference data (FORCE RLS lifted for that one transaction because the owner is not a superuser, then restored), and creates one verified demo owner. No credential is in the repository; all secrets are Vercel environment variables (`ARTIFY_DATABASE_URL_*`, `ARTIFY_ROLE_PASSWORD_*`, JWT and MFA keys, demo credentials).

## What does not work here (by construction)

* **No background worker, no Redis**: rate limits are per instance (memory), so they are weaker than production; processing runs in-process only (`processing_mode = background`) and is slow or unavailable.
* **No OCR**: the Tesseract binary is not in the function image, so document processing answers `provider_not_configured`.
* **Document files are not durable**: local storage under `/tmp` is per instance. Do not use real documents.
* **No e-mail**: the mailer is `console`; sign-up verification mails are not delivered, so only the seeded demo owner (or accounts created by an administrator) can sign in. MFA enrollment is required at first sign-in for owners.
* Environment name is `ci` (the staging/production guards require Redis, SMTP and S3).
* Data is synthetic/demo only (invariant 11).

## Operating notes

* Re-deploy: push to branch `deploy-vercel`, create a production deployment of both projects from it.
* Reset: drop the schema in the Supabase project; the next request re-bootstraps.
* The API project has Vercel Authentication disabled so the web server can call it; it is a public API by design (authentication is the application's own).
