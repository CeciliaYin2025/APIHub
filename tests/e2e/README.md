# Admin UI E2E tests (Playwright)

Auto-provisions a disposable Postgres container + the app, then drives the
Admin UI in headless chromium.

## Specs
- `smoke.spec.js` — the login page renders.
- `query-console.spec.js` — definition picker, param autofill, Parameter Reference.
- `raw-json-copy.spec.js` — the Copy buttons on the console result blocks.
- `sql-definition-groups.spec.js` — the SQL Definitions list grouped by
  `group_name`: group ordering and per-group counts, the pane switch, the
  selection surviving a detail round-trip, and the create form deriving Name
  (`<group>_`) and Permission (`query:<name>`) from the Group while leaving
  hand-edited values alone. It seeds its own `ZZ_*` definitions because the
  global fixture predates the field — those ungrouped fixtures are what the
  `(ungrouped)` assertion covers.
- `usage-report.spec.js` — the API Usage Report. Seeds audited query executions
  (deliberately failing: there is no Oracle here, and failed calls are audited
  too), then asserts the aggregated call/success/failure counts, the drill-down
  to individual requests, the expanded `params` JSON, and that a non-superuser
  never sees the report's nav entry. This is the only place the aggregation
  numbers are checked against a real PostgreSQL — `pytest` has no live
  database, so `tests/test_audit_report_service.py` covers only the query
  building and pure helpers.

- `write-definitions.spec.js` — named write definitions against a **real**
  PostgreSQL: creates the definition through the UI (columns loaded from the
  table), then checks that `replace` is idempotent per key, never touches a row
  another writer owns, coerces dates, lets the DB default fill omitted columns,
  that dry run counts without writing, and the 400 guards.

## Data sources
`test-env.js` sets `DATABASE_DEFINITIONS_DIR` to `fixtures/databases/`, so the
run never uses a developer's own `app/database_definitions/databases.yaml`:
- `default` — Oracle at `127.0.0.1:1`, deliberately unreachable. The seeded
  definitions are dummy SQL and `usage-report.spec.js` relies on executions
  against it failing.
- `pg_test` — the throwaway Postgres container itself; the target of the
  write-definition specs and the alias the query-console DB-switch specs select.

## Prerequisites
- Docker (pulls `postgres:16-alpine` on first run)
- The project's Python venv with app dependencies installed
- Node 24+

## Run
```bash
cd tests/e2e
npm install
npx playwright install chromium
npm test
```

`global-setup.js` starts Postgres on `55432`, seeds the DB (`admin`/`changeme`),
launches uvicorn on `8199`, and seeds an `e2e_types` SQL definition.
`global-teardown.js` tears it all down. Nothing is left running.
