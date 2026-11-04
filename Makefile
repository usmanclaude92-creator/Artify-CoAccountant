# One command vocabulary for developers and CI. Requires: uv, pnpm, PostgreSQL (see PG* variables).
PGHOST ?= /var/run/postgresql
PGPORT ?= 54329
PGUSER ?= postgres
export PGHOST PGPORT PGUSER
API = uv run --project services/api

.PHONY: install db-reset test-api lint typecheck boundaries openapi openapi-check openapi-breaking client web-build web-test e2e check-docs audit hooks check

install:            ## install Python and JS dependencies from lockfiles
	uv sync --project services/api
	pnpm install --frozen-lockfile

db-reset:           ## drop/recreate DB $(DB) (default artify_dev): migrate, create dev login roles, seed
	infrastructure/database/reset_dev_db.sh $(or $(DB),artify_dev)

test-api:           ## API tests against a real PostgreSQL (rebuilds database artify_test)
	$(API) pytest services/api/tests

boundaries:         ## module-boundary contracts (import-linter)
	cd services/api && uv run lint-imports

lint: boundaries    ## ruff + import-linter + eslint
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

openapi-breaking:   ## fail on breaking API changes vs git ref BASE (default HEAD~1); see packages/api-client/breaking-changes-allowed.txt
	$(API) python services/api/scripts/check_openapi_breaking.py git:$(or $(BASE),HEAD~1) packages/api-client/openapi.json --allow packages/api-client/breaking-changes-allowed.txt

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

audit:              ## dependency vulnerability scan (Python: pip-audit, JS: pnpm audit)
	uv export --project services/api --no-hashes --no-emit-project | grep -v '^#' > /tmp/artify-req.txt
	uvx pip-audit -r /tmp/artify-req.txt --no-deps --disable-pip
	pnpm audit --prod

hooks:              ## install git pre-commit hooks
	uvx pre-commit install

check: lint typecheck openapi-check test-api check-docs web-test web-build   ## everything except e2e
