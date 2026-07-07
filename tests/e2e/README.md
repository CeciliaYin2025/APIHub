# Admin UI E2E tests (Playwright)

Auto-provisions a disposable Postgres container + the app, then drives the
Query Console feature in headless chromium.

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
