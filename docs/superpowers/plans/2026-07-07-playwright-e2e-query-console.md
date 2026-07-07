# Playwright E2E Query Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Playwright E2E test harness under `tests/e2e/` that auto-provisions a disposable Postgres + the app, then verifies the Query Console feature (DB-filtered dropdown, example-parameter autofill, switch-DB-clears) through a real browser.

**Architecture:** A self-contained Node/Playwright project in `tests/e2e/`. `global-setup.js` owns lifecycle ordering: start Postgres container → seed the platform store via `init_db` → launch uvicorn → seed an `e2e_types` SQL definition via the API. `global-teardown.js` reverses it. Specs drive chromium against the running app. Kept isolated from the pytest suite.

**Tech Stack:** Node 24, `@playwright/test` (chromium, headless), Docker (disposable `postgres:16-alpine`), the project's existing Python venv for `init_db` + `uvicorn`.

## Global Constraints

- All new files live under `tests/e2e/`. No changes to `app/` application code.
- Ports (verbatim): app `8199`, Postgres `55432`. Container name: `apihub-e2e-pg`. Admin creds seeded by `init_db`: `admin` / `changeme`.
- Shared app env (verbatim): `POSTGRES_HOST=localhost`, `POSTGRES_PORT=55432`, `POSTGRES_USER=api_hub`, `POSTGRES_PASSWORD=changeme`, `POSTGRES_DB=api_hub`, `SECRET_KEY=e2e-secret-key`. No `ROOT_PATH`.
- Example-value mapping under test (verbatim): `string→"example"`, `number→0`, `date→"2024-01-01"`.
- CommonJS modules only (`require`/`module.exports`) — do NOT add `"type":"module"` to `tests/e2e/package.json`.
- Resolve the Python interpreter to the repo `.venv` (`.venv/Scripts/python.exe` on Windows, `.venv/bin/python` otherwise) so spawned `init_db`/`uvicorn` use the project's dependencies.

---

### Task 1: Scaffold the Playwright project, config, and pytest isolation

**Files:**
- Create: `tests/e2e/package.json`, `tests/e2e/.gitignore`, `tests/e2e/test-env.js`, `tests/e2e/playwright.config.js`, `tests/e2e/smoke.spec.js`, `tests/e2e/README.md`
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`)

**Interfaces:**
- Produces: `test-env.js` exports `{ APP_PORT, PG_PORT, CONTAINER, BASE_URL, REPO_ROOT, PYTHON, APP_ENV }` consumed by setup/teardown in Task 2.

- [ ] **Step 1: Create `tests/e2e/package.json`**

```json
{
  "name": "oracle-api-hub-e2e",
  "private": true,
  "version": "0.0.0",
  "description": "Playwright E2E tests for the Oracle API Hub Admin UI",
  "scripts": {
    "test": "playwright test",
    "install-browsers": "playwright install chromium"
  },
  "devDependencies": {
    "@playwright/test": "^1.49.0"
  }
}
```

- [ ] **Step 2: Create `tests/e2e/.gitignore`**

```
node_modules/
playwright-report/
test-results/
.e2e-state.json
.uvicorn.log
```

- [ ] **Step 3: Create `tests/e2e/test-env.js`**

```javascript
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const APP_PORT = 8199;
const PG_PORT = 55432;
const CONTAINER = 'apihub-e2e-pg';
const BASE_URL = `http://127.0.0.1:${APP_PORT}`;

function resolvePython() {
  const win = path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe');
  const nix = path.join(REPO_ROOT, '.venv', 'bin', 'python');
  if (fs.existsSync(win)) return win;
  if (fs.existsSync(nix)) return nix;
  return process.env.E2E_PYTHON || 'python';
}

const APP_ENV = {
  POSTGRES_HOST: 'localhost',
  POSTGRES_PORT: String(PG_PORT),
  POSTGRES_USER: 'api_hub',
  POSTGRES_PASSWORD: 'changeme',
  POSTGRES_DB: 'api_hub',
  SECRET_KEY: 'e2e-secret-key',
};

module.exports = {
  REPO_ROOT,
  APP_PORT,
  PG_PORT,
  CONTAINER,
  BASE_URL,
  PYTHON: resolvePython(),
  APP_ENV,
};
```

- [ ] **Step 4: Create `tests/e2e/playwright.config.js`**

```javascript
const { defineConfig } = require('@playwright/test');
const { BASE_URL } = require('./test-env');

module.exports = defineConfig({
  testDir: '.',
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  globalSetup: require.resolve('./global-setup.js'),
  globalTeardown: require.resolve('./global-teardown.js'),
  use: {
    baseURL: BASE_URL,
    headless: true,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
```

- [ ] **Step 5: Create `tests/e2e/smoke.spec.js`** (proves the harness once provisioning lands in Task 2)

```javascript
const { test, expect } = require('@playwright/test');

test('admin login page loads', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.locator('#loginUser')).toBeVisible();
  await expect(page.locator('#loginPass')).toBeVisible();
});
```

- [ ] **Step 6: Create `tests/e2e/README.md`**

```markdown
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
```

- [ ] **Step 7: Isolate the pytest suite from the Node tree**

In `pyproject.toml`, under `[tool.pytest.ini_options]` (which already has `testpaths = ["tests"]`), add:

```toml
norecursedirs = ["tests/e2e", "node_modules", ".venv"]
```

- [ ] **Step 8: Install deps and the browser**

Run:
```bash
cd tests/e2e && npm install && npx playwright install chromium
```
Expected: `@playwright/test` installed under `tests/e2e/node_modules`; chromium downloaded.

- [ ] **Step 9: Verify config loads and pytest is unaffected**

Run:
```bash
cd tests/e2e && npx playwright test --list
```
Expected: lists `smoke.spec.js › admin login page loads` (config + spec parse; it does NOT run yet).

Run from repo root:
```bash
python -m pytest -q
```
Expected: same result as before this task (the pre-existing `test_sql_registry.py` failures may remain; no NEW failures, and no attempt to collect `tests/e2e`).

- [ ] **Step 10: Commit**

```bash
git add tests/e2e/package.json tests/e2e/.gitignore tests/e2e/test-env.js tests/e2e/playwright.config.js tests/e2e/smoke.spec.js tests/e2e/README.md pyproject.toml
git commit -m "test(e2e): scaffold Playwright project, config, and pytest isolation"
```

---

### Task 2: Auto-provisioning (global setup + teardown)

**Files:**
- Create: `tests/e2e/global-setup.js`, `tests/e2e/global-teardown.js`

**Interfaces:**
- Consumes: `test-env.js` exports from Task 1.
- Produces: a running app at `BASE_URL` with `admin`/`changeme` seeded and an `e2e_types` definition (params `n:number, d:date, s:string`) under db_alias `default`; a `.e2e-state.json` file `{ uvicornPid, container }` consumed by teardown.

- [ ] **Step 1: Create `tests/e2e/global-setup.js`**

```javascript
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { REPO_ROOT, APP_PORT, PG_PORT, CONTAINER, BASE_URL, PYTHON, APP_ENV } = require('./test-env');

const STATE_FILE = path.join(__dirname, '.e2e-state.json');
const LOG_FILE = path.join(__dirname, '.uvicorn.log');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { encoding: 'utf8', ...opts });
  if (res.error) throw res.error;
  return res;
}

async function waitFor(label, fn, { tries = 60, delay = 1000 } = {}) {
  for (let i = 0; i < tries; i++) {
    try {
      if (await fn()) return;
    } catch (_) { /* keep polling */ }
    await sleep(delay);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

module.exports = async () => {
  const env = { ...process.env, ...APP_ENV };

  // 1. Fresh Postgres container.
  console.log('[e2e] starting Postgres container...');
  run('docker', ['rm', '-f', CONTAINER]);
  const up = run('docker', ['run', '-d', '--name', CONTAINER,
    '-e', 'POSTGRES_USER=api_hub', '-e', 'POSTGRES_PASSWORD=changeme',
    '-e', 'POSTGRES_DB=api_hub', '-p', `${PG_PORT}:5432`, 'postgres:16-alpine']);
  if (up.status !== 0) throw new Error(`docker run failed: ${up.stderr}`);

  // 2. Wait for Postgres to accept connections.
  await waitFor('Postgres ready', () =>
    run('docker', ['exec', CONTAINER, 'pg_isready', '-U', 'api_hub']).status === 0);

  // 3. Seed tables + admin user.
  console.log('[e2e] running init_db...');
  const init = run(PYTHON, ['-m', 'scripts.init_db'], { cwd: REPO_ROOT, env });
  if (init.status !== 0) throw new Error(`init_db failed:\n${init.stdout}\n${init.stderr}`);

  // 4. Launch uvicorn (detached; logs to file).
  console.log('[e2e] launching uvicorn...');
  const logFd = fs.openSync(LOG_FILE, 'w');
  const app = spawn(PYTHON, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(APP_PORT)],
    { cwd: REPO_ROOT, env, stdio: ['ignore', logFd, logFd], detached: true });
  app.unref();

  // 5. Wait for health.
  await waitFor('app health', async () => {
    const res = await fetch(`${BASE_URL}/api/v1/health`);
    return res.ok;
  });

  // 6. Seed an e2e_types definition (dummy SQL, never executed) covering all param types.
  console.log('[e2e] seeding e2e_types definition...');
  const form = new URLSearchParams({ username: 'admin', password: 'changeme' });
  const tokRes = await fetch(`${BASE_URL}/api/v1/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  });
  const { access_token } = await tokRes.json();
  const defRes = await fetch(`${BASE_URL}/api/v1/default/queries/definitions`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${access_token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: 'e2e_types',
      db_alias: 'default',
      description: 'E2E fixture: all param types',
      sql: 'SELECT 1',
      permission: '',
      params: {
        n: { type: 'number', required: false, description: '' },
        d: { type: 'date', required: false, description: '' },
        s: { type: 'string', required: false, description: '' },
      },
    }),
  });
  if (!defRes.ok) throw new Error(`seed e2e_types failed: ${defRes.status} ${await defRes.text()}`);

  fs.writeFileSync(STATE_FILE, JSON.stringify({ uvicornPid: app.pid, container: CONTAINER }));
  console.log(`[e2e] ready at ${BASE_URL} (uvicorn pid ${app.pid})`);
};
```

- [ ] **Step 2: Create `tests/e2e/global-teardown.js`**

```javascript
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { CONTAINER } = require('./test-env');

const STATE_FILE = path.join(__dirname, '.e2e-state.json');

function killPid(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(pid), '/T', '/F']);
    } else {
      process.kill(pid);
    }
  } catch (_) { /* already gone */ }
}

module.exports = async () => {
  let state = {};
  try { state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch (_) { /* none */ }
  killPid(state.uvicornPid);
  spawnSync('docker', ['rm', '-f', state.container || CONTAINER]);
  try { fs.unlinkSync(STATE_FILE); } catch (_) { /* none */ }
  console.log('[e2e] torn down');
};
```

- [ ] **Step 3: Run the smoke spec end-to-end (proves provisioning)**

Run:
```bash
cd tests/e2e && npm test
```
Expected: `global-setup` logs (Postgres → init_db → uvicorn → seed e2e_types), then `smoke.spec.js › admin login page loads` PASSES (`1 passed`), then `global-teardown` logs. Afterward `docker ps -a --filter name=apihub-e2e-pg` shows no container.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/global-setup.js tests/e2e/global-teardown.js
git commit -m "test(e2e): auto-provision Postgres + app in global setup/teardown"
```

---

### Task 3: Query Console feature specs

**Files:**
- Create: `tests/e2e/query-console.spec.js`

**Interfaces:**
- Consumes: the running app + seeded `e2e_types` from Task 2.

- [ ] **Step 1: Create `tests/e2e/query-console.spec.js`**

```javascript
const { test, expect } = require('@playwright/test');

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPass', 'changeme');
  await page.click('button:has-text("Login")');
  // Top bar appears only after a successful login.
  await expect(page.locator('#dbSelect')).toBeVisible();
}

async function openQueryConsole(page) {
  await page.click('.sidebar-nav a[data-page="query-console"]');
  await expect(page.locator('#qcName')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await login(page);
  await openQueryConsole(page);
});

test('dropdown lists only the current DB\'s queries', async ({ page }) => {
  const opts = await page.$$eval('#qcName option', (els) => els.map((e) => e.value));
  expect(opts).toEqual(
    expect.arrayContaining(['', 'departments', 'employees', 'inventory_view', 'sglp0021_temp', 'e2e_types'])
  );
});

test('selecting a string-param query autofills an example', async ({ page }) => {
  await page.selectOption('#qcName', 'employees');
  await expect
    .poll(async () => JSON.parse(await page.inputValue('#qcParams')))
    .toEqual({ dept: 'example' });
});

test('selecting a multi-param query autofills all params', async ({ page }) => {
  await page.selectOption('#qcName', 'inventory_view');
  await expect
    .poll(async () => JSON.parse(await page.inputValue('#qcParams')))
    .toEqual({ product_code: 'example', warehouse: 'example' });
});

test('example values follow param type (number/date/string)', async ({ page }) => {
  await page.selectOption('#qcName', 'e2e_types');
  await expect
    .poll(async () => JSON.parse(await page.inputValue('#qcParams')))
    .toEqual({ n: 0, d: '2024-01-01', s: 'example' });
});

test('Request Body preview reflects the selected query and params', async ({ page }) => {
  await page.selectOption('#qcName', 'employees');
  const body = await page.textContent('#qcBodyPreview');
  expect(body).toContain('"query_name": "employees"');
  expect(body).toContain('"dept": "example"');
});

test('switching the DB clears the selection and parameters', async ({ page }) => {
  await page.selectOption('#qcName', 'employees');
  await page.selectOption('#dbSelect', 'pg_test');
  // The change handler re-loads the picker and clears async; poll until settled.
  await expect.poll(async () => await page.inputValue('#qcName')).toBe('');
  await expect.poll(async () => await page.inputValue('#qcParams')).toBe('{}');
  const opts = await page.$$eval('#qcName option', (els) => els.map((e) => e.value));
  expect(opts).toEqual(['']);
});
```

- [ ] **Step 2: Run the full E2E suite**

Run:
```bash
cd tests/e2e && npm test
```
Expected: `smoke.spec.js` (1) + `query-console.spec.js` (6) all PASS — `7 passed`. Teardown removes the container.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/query-console.spec.js
git commit -m "test(e2e): verify Query Console DB-filter, autofill, and clear-on-DB-switch"
```

---

## Self-Review

**Spec coverage:**
- Framework Playwright/chromium/headless → Task 1 config. ✓
- Auto-provision (Postgres + init_db + uvicorn + teardown) → Task 2 global-setup/teardown. ✓
- Seed `e2e_types` for type mapping → Task 2 Step 1 (step 6 of setup). ✓
- pytest isolation → Task 1 Step 7. ✓
- Test cases 1–6 (dropdown filter, string autofill, multi-param, type mapping, request-body preview, switch-DB clears) → Task 3 spec, one `test()` each. ✓
- "Full-function verification" deliverable (run + green) → Task 3 Step 2. ✓

**Placeholder scan:** No TBD/TODO; every file's full contents shown. ✓

**Type/name consistency:** `test-env.js` exports (`REPO_ROOT, APP_PORT, PG_PORT, CONTAINER, BASE_URL, PYTHON, APP_ENV`) match their uses in `global-setup.js`/`global-teardown.js`/`playwright.config.js`. State file `.e2e-state.json` shape `{ uvicornPid, container }` written in setup, read in teardown. DOM selectors (`#loginUser`, `#loginPass`, `#dbSelect`, `.sidebar-nav a[data-page="query-console"]`, `#qcName`, `#qcParams`, `#qcBodyPreview`) match `app/admin/js/app.js`. ✓

**Note on pre-existing failures:** `tests/test_sql_registry.py` fails independently of this work (documented earlier); Task 1 Step 9 only requires no NEW pytest failures.
