# One command vocabulary for developers and CI. Requires: uv, pnpm, PostgreSQL (see PG* variables).
PGHOST ?= /var/run/postgresql
PGPORT ?= 54329
PGUSER ?= postgres
export PGHOST PGPORT PGUSER
API = uv run --project services/api

.PHONY: install db-reset test-api lint typecheck openapi openapi-check client web-build web-test e2e check-docs check

install:            ## install Python and JS dependencies from lockfiles
	uv sync --project services/api
	pnpm install --frozen-lockfile

db-reset:           ## drop/recreate DB $(DB) (default artify_dev): migrate, create dev login roles, seed
	infrastructure/database/reset_dev_db.sh $(or $(DB),artify_dev)

test-api:           ## API tests against a real PostgreSQL (rebuilds database artify_test)
	$(API) pytest services/api/tests

lint:               ## ruff + eslint
	$(API) ruff check services/api
	$(API) ruff format --check services/api
	pnpm --filter @artify/web lint

typecheck:          ## mypy (strict) + tsc
	cd services/api && uv run mypy src
	pnpm -r --if-present typecheck

openapi:            ## regenerate packages/api-client/openapi.json from the FastAPI app
	$(API) python services/api/scripts/export_openapi.py

openapi-check:      ## fail if the committed OpenAPI contract is stale
	$(API) python services/api/scripts/export_openapi.py --check

client: openapi     ## regenerate the typed TypeScript client
	pnpm --filter @artify/api-client generate

web-build:
	pnpm --filter @artify/web build

web-test:
	pnpm --filter @artify/web test

e2e:                ## browser end-to-end (API + built web app + Playwright)
	infrastructure/dev/e2e.sh

check-docs:
	python3 tools/check_docs.py

check: lint typecheck openapi-check test-api check-docs web-test web-build   ## everything except e2e
