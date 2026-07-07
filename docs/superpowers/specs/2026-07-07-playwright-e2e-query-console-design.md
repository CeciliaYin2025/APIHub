# Playwright E2E test framework — Query Console full-function verification

Date: 2026-07-07
Status: Approved (design)
Scope: New E2E test harness under `tests/e2e/`; a `pyproject.toml` pytest-isolation tweak. No application code changes.

## Problem

The Admin UI SPA (`app/admin/js/app.js`) has no automated test coverage — the Query Console feature added this session was verified only by hand in Chrome. We want a repeatable, automated full-function verification that drives the real browser against a real app + database, so the feature (and regressions) can be checked with one command.

## Decisions (from brainstorming)

1. **Framework: Playwright** (`@playwright/test`, chromium, headless). Drives the real Admin UI end-to-end.
2. **Scope: the Query Console feature** — login → DB-filtered query dropdown → select query auto-fills example parameters → switch DB clears selection + params. Swagger Authorize is out of scope (its relative-tokenUrl fix is already covered by the Python unit test `tests/test_root_path.py::test_oauth2_token_url_is_relative`).
3. **Provisioning: auto** — Playwright `globalSetup` stands up a disposable Postgres container + seeds the platform store + launches the app; `globalTeardown` removes it. One command (`npm test`) runs everything. Requires Docker + the project's Python env.
4. **Seed an `e2e_types` definition** with number/date/string params (dummy SQL, never executed) so the example-type mapping (`number→0`, `date→"2024-01-01"`, `string→"example"`) is verified through the real UI, not just in isolation.

## Architecture

All new files live under `tests/e2e/` (kept separate from the pytest suite in `tests/`).

```
tests/e2e/
  package.json          # @playwright/test devDep + scripts
  playwright.config.js  # chromium, headless, baseURL, globalSetup/Teardown
  test-env.js           # shared constants: ports, POSTGRES_* + SECRET_KEY env
  global-setup.js       # provision Postgres + init_db + uvicorn + seed e2e_types
  global-teardown.js    # tear everything down
  query-console.spec.js # the tests
  .gitignore            # node_modules, playwright-report, test-results, .e2e-state.json, .uvicorn.log
  README.md             # how to run
```

### Provisioning flow (`global-setup.js`)

`globalSetup` owns the full lifecycle ordering (rather than Playwright's `webServer`) to avoid a race where the app starts before Postgres is ready:

1. `docker rm -f apihub-e2e-pg` (clean any stale container), then `docker run -d --name apihub-e2e-pg -e POSTGRES_USER=api_hub -e POSTGRES_PASSWORD=changeme -e POSTGRES_DB=api_hub -p 55432:5432 postgres:16-alpine`.
2. Poll `docker exec apihub-e2e-pg pg_isready -U api_hub` until ready (timeout ~30s).
3. Run `python -m scripts.init_db` (wait for exit 0) with the shared test env → creates tables + admin/changeme.
4. Spawn `python -m uvicorn app.main:app --host 127.0.0.1 --port 8199` (detached, logs → `.uvicorn.log`) with the shared test env. No `ROOT_PATH`.
5. Poll `http://127.0.0.1:8199/api/v1/health` until `200` (timeout ~30s).
6. Obtain an admin token (`POST /api/v1/auth/token`), then `POST /api/v1/default/queries/definitions` to seed `e2e_types` (params `n:number, d:date, s:string`, dummy sql `SELECT 1`).
7. Write `{ uvicornPid, containerName }` to `tests/e2e/.e2e-state.json` for teardown.

### Teardown (`global-teardown.js`)

Read `.e2e-state.json`; `process.kill(uvicornPid)`; `docker rm -f apihub-e2e-pg`; delete the state file. Tolerate missing pieces (idempotent).

### Shared env (`test-env.js`)

Exports `APP_PORT=8199`, `PG_PORT=55432`, `CONTAINER='apihub-e2e-pg'`, `BASE_URL='http://127.0.0.1:8199'`, and `APP_ENV` = `{ POSTGRES_HOST:'localhost', POSTGRES_PORT:'55432', POSTGRES_USER:'api_hub', POSTGRES_PASSWORD:'changeme', POSTGRES_DB:'api_hub', SECRET_KEY:'e2e-secret-key' }` merged over `process.env`. Used by both `init_db` (setup) and `uvicorn` (setup) so config never drifts.

### Config (`playwright.config.js`)

`testDir: '.'`, `globalSetup`/`globalTeardown` pointing to the modules, `use.baseURL` = BASE_URL, `use.headless: true`, single chromium project, `fullyParallel: false` (one app instance), reasonable `timeout`.

## Test cases (`query-console.spec.js`)

A `beforeEach` logs in and opens the Query Console (fill `#loginUser`/`#loginPass` with admin/changeme, submit, wait for dashboard, click the Query Console nav link).

1. **DB-filtered dropdown** — with DB `default`, `#qcName` options include `departments, employees, inventory_view, sglp0021_temp, e2e_types` plus the leading placeholder.
2. **String param autofill** — select `employees`; `JSON.parse(#qcParams.value)` deep-equals `{ dept: "example" }`.
3. **Multi-param autofill** — select `inventory_view`; params deep-equal `{ product_code: "example", warehouse: "example" }`.
4. **Type mapping** — select `e2e_types`; params deep-equal `{ n: 0, d: "2024-01-01", s: "example" }` (covers number/date/string).
5. **Request Body preview** — after selecting a query, `#qcBodyPreview` text contains the `query_name` and the auto-filled params.
6. **Switch DB clears** — set `#dbSelect` to `pg_test` and dispatch change; `#qcName.value === ''`, its options reduce to only the placeholder, and `#qcParams.value === '{}'`.

## pytest isolation

Add to `pyproject.toml` `[tool.pytest.ini_options]`: `norecursedirs = ["tests/e2e", "node_modules", ".venv"]` so `pytest -v` never walks the Node test tree.

## Verification

After implementation, run `cd tests/e2e && npm install && npx playwright install chromium && npm test` and confirm all specs pass (the "full-function verification" deliverable). Also confirm `pytest -q` still behaves as before (unaffected).

## Non-goals (YAGNI)

- No changes to `app.js` (E2E drives the real UI; no exports/refactor needed).
- No Swagger Authorize E2E (covered by the Python unit test).
- No CI workflow file (can be added later).
- No cross-browser matrix (chromium only).
