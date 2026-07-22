# Query Console JSON Copy + Parameter Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-click "Copy" button to every Raw JSON result block in the Admin UI, and give Query Console a two-column parameter panel that shows each SQL definition's declared parameter name/type/required/description next to the JSON input box.

**Architecture:** Pure frontend change in the vanilla-JS Admin SPA (`app/admin/js/app.js` + `app/admin/css/style.css`). A shared `rawJsonBlock(id, data)` helper stores the plain-text JSON in an in-memory registry and renders a `📋 Copy` button next to the existing syntax-highlighted `<pre>`; it replaces 5 existing inline `syntaxHighlight(JSON.stringify(...))` call sites without changing their surrounding collapse/expand structure. Query Console's parameter panel is a new `qcRenderParamsInfo(def)` function that renders straight from the already-fetched `SqlDefinitionOut.params` map — no new API calls.

**Tech Stack:** Vanilla JS (no build step, no framework), served as static files under `/admin`. Tests: Playwright e2e (`tests/e2e/`), run against a real FastAPI + Postgres instance started by `global-setup.js`; no dedicated JS unit test runner exists in this repo, so "failing test" here means the Playwright assertion times out against the pre-change UI.

## Global Constraints

- Copy must place **plain-text JSON** on the clipboard — never the syntax-highlighted HTML markup that `<pre>` visually shows.
- Existing collapse/expand structure per result block must not change: sites that used `<details>` keep `<details>`; sites that used a bare `<pre>` keep a bare `<pre>` (only add a toolbar+button above it).
- The Parameter Reference panel always shows the **full declared parameter list** from the selected SQL definition, regardless of what the user has typed into the `Parameters (JSON)` textarea.
- The Parameter Reference feature applies **only to Query Console** — Procedure Console and Write Console are not touched (their params aren't backed by a stored definition).
- Follow existing code style in `app/admin/js/app.js`: `window.xxx = function(){}` for handlers invoked from `onclick`/`onchange` HTML attributes, plain `function xxx(){}` for internal helpers, `esc()` for any user-controlled string interpolated into HTML.

---

### Task 1: Shared JSON-copy helper, applied to Query Console

**Files:**
- Modify: `app/admin/js/app.js:241` (insert new helper block after `syntaxHighlight`, before the `Page Renderers` section comment)
- Modify: `app/admin/js/app.js:1672-1691` (`executeQuery`'s success branch)
- Test: `tests/e2e/query-console.spec.js` (append new test)

**Interfaces:**
- Produces: `registerCopyPayload(id: string, text: string): void`, `window.copyPayload(id: string, label: string): Promise<void>`, `rawJsonBlock(id: string, data: any): string` — all three are used by Tasks 2 and 4 as-is, do not rename.

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/query-console.spec.js` (after the last existing `test(...)` block):

```js
test('copying the Raw JSON response places plain JSON on the clipboard', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const mockResult = { total: 1, columns: ['dept'], data: [{ dept: 'IT' }] };
  await page.route('**/api/v1/default/queries/execute', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(mockResult),
  }));

  await page.selectOption('#qcName', 'employees');
  await page.click('button:has-text("Execute Query")');
  await page.click('#qcResult summary');
  await page.click('#qcResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd tests/e2e && npx playwright test query-console.spec.js -g "copying the Raw JSON response"`
Expected: FAIL — `page.click('#qcResult button:has-text("Copy")')` times out because no Copy button exists in the Raw JSON Response block yet.

- [ ] **Step 3: Add the shared helper**

In `app/admin/js/app.js`, right after the `syntaxHighlight` function (ends at line 241) and before the `/* ══...  Page Renderers` comment (line 243), insert:

```js
/* ── Raw JSON copy (shared across consoles) ──────────────────────────────
   The visible <pre> holds syntax-highlighted HTML, not plain text, so the
   copyable plain-text JSON is kept in a small registry keyed by a per-block
   id and read back when the Copy button is clicked. */
const _copyRegistry = {};

function registerCopyPayload(id, text) {
  _copyRegistry[id] = text;
}

window.copyPayload = async function(id, label) {
  const text = _copyRegistry[id];
  if (!text) return toast('Nothing to copy', 'error');
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} copied`, 'success');
  } catch {
    toast('Copy failed', 'error');
  }
};

// Renders a syntax-highlighted <pre> plus a Copy button for `data`,
// registering the plain-text JSON under `id`. Callers embed the result
// wherever a raw JSON response used to be shown directly.
function rawJsonBlock(id, data) {
  const json = JSON.stringify(data, null, 2);
  registerCopyPayload(id, json);
  return `
    <div class="qc-code-toolbar" style="margin-bottom:4px">
      <span></span>
      <button class="btn btn-outline btn-sm" onclick="copyPayload('${id}','JSON')">&#128203; Copy</button>
    </div>
    <pre>${syntaxHighlight(json)}</pre>`;
}
```

- [ ] **Step 4: Use it in Query Console's result**

In `executeQuery` (`app/admin/js/app.js`), find:

```js
      <details class="mt-4"><summary class="text-sm" style="cursor:pointer">Raw JSON Response</summary>
        <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
      </details>
```

Replace with:

```js
      <details class="mt-4"><summary class="text-sm" style="cursor:pointer">Raw JSON Response</summary>
        ${rawJsonBlock('qcResultJson', result)}
      </details>
```

- [ ] **Step 5: Run the test again to verify it passes**

Run: `cd tests/e2e && npx playwright test query-console.spec.js -g "copying the Raw JSON response"`
Expected: PASS.

- [ ] **Step 6: Run the full spec file to confirm no regressions**

Run: `cd tests/e2e && npx playwright test query-console.spec.js`
Expected: All tests PASS (the pre-existing 6 tests plus the new one).

- [ ] **Step 7: Commit**

```bash
git add app/admin/js/app.js tests/e2e/query-console.spec.js
git commit -m "feat(admin-ui): add copy button to Query Console raw JSON result"
```

---

### Task 2: Query Console — two-column Parameter Reference panel

**Files:**
- Modify: `app/admin/js/app.js:1427-1430` (`renderQueryConsole`'s Parameters form-group)
- Modify: `app/admin/js/app.js:1481-1489` (`qcOnSelectQuery`)
- Modify: `app/admin/js/app.js:112-127` (`dbSelect` change handler — must also clear the panel on DB switch)
- Modify: `app/admin/css/style.css` (new rules near the existing Query Console section, after line 245)
- Modify: `tests/e2e/global-setup.js` (seed fixture needs real `description`/`required` values to assert against)
- Test: `tests/e2e/query-console.spec.js` (append new tests)

**Interfaces:**
- Consumes: `qcDefs` (existing global, array of `SqlDefinitionOut`-shaped objects with `.params: {[name]: {type, required, description}}`), `esc()` (existing HTML-escape helper).
- Produces: `qcRenderParamsInfo(def: {params: object} | null): void` — renders into `#qcParamsInfo`; called by `qcOnSelectQuery` and by the `dbSelect` change handler.

- [ ] **Step 1: Update the e2e fixture to carry real param metadata**

In `tests/e2e/global-setup.js`, replace:

```js
  const p = (type) => ({ type, required: false, description: '' });
  const definitions = [
    { name: 'employees', params: { dept: p('string') } },
    { name: 'inventory_view', params: { product_code: p('string'), warehouse: p('string') } },
    { name: 'e2e_types', params: { n: p('number'), d: p('date'), s: p('string') } },
  ];
```

with:

```js
  const p = (type, opts = {}) => ({ type, required: !!opts.required, description: opts.description || '' });
  const definitions = [
    { name: 'employees', params: { dept: p('string', { description: 'Filter by department code' }) } },
    { name: 'inventory_view', params: {
      product_code: p('string', { required: true, description: 'SKU or product code to filter by' }),
      warehouse: p('string', { description: 'Warehouse location code' }),
    } },
    { name: 'e2e_types', params: { n: p('number'), d: p('date'), s: p('string') } },
  ];
```

This keeps every existing assertion valid (`qcExampleParams` only reads `.type`, never `.description`/`.required`) while giving the new tests real description/required-flag variety to check: `employees.dept` has a description, `inventory_view.product_code` is required, `inventory_view.warehouse` has no description, and `e2e_types`' three params have neither — covering all rendering branches.

- [ ] **Step 2: Write the failing tests**

Append to `tests/e2e/query-console.spec.js`:

```js
test('Parameter Reference shows a placeholder before a query is selected', async ({ page }) => {
  const text = await page.textContent('#qcParamsInfo');
  expect(text).toContain('Select a query to see parameter details.');
});

test('Parameter Reference shows type, required/optional and description per param', async ({ page }) => {
  await page.selectOption('#qcName', 'inventory_view');
  const info = page.locator('#qcParamsInfo');
  const productCode = info.locator('.qc-param-item', { hasText: 'product_code' });
  await expect(productCode).toContainText('string');
  await expect(productCode).toContainText('required');
  await expect(productCode).toContainText('SKU or product code to filter by');

  const warehouse = info.locator('.qc-param-item', { hasText: 'warehouse' });
  await expect(warehouse).toContainText('optional');
  await expect(warehouse.locator('.qc-param-desc')).toHaveCount(0);
});

test('Parameter Reference omits the description block when there is none', async ({ page }) => {
  await page.selectOption('#qcName', 'e2e_types');
  const info = page.locator('#qcParamsInfo');
  await expect(info.locator('.qc-param-item')).toHaveCount(3);
  await expect(info.locator('.qc-param-desc')).toHaveCount(0);
});

test('switching the DB resets Parameter Reference to the placeholder', async ({ page }) => {
  await page.selectOption('#qcName', 'employees');
  await expect(page.locator('#qcParamsInfo')).toContainText('Filter by department code');
  await page.selectOption('#dbSelect', 'pg_test');
  await expect
    .poll(async () => page.textContent('#qcParamsInfo'))
    .toContain('Select a query to see parameter details.');
});
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd tests/e2e && npx playwright test query-console.spec.js -g "Parameter Reference"`
Expected: FAIL — `#qcParamsInfo` does not exist yet in the DOM (the Parameters form-group is still a single column).

- [ ] **Step 4: Split the Parameters form-group into two columns**

In `renderQueryConsole()` (`app/admin/js/app.js`), find:

```html
          <div class="form-group query-console">
            <label>Parameters (JSON)</label>
            <textarea id="qcParams" placeholder='{"dept": "IT"}'>{}</textarea>
          </div>
```

Replace with:

```html
          <div class="form-row">
            <div class="form-group query-console">
              <label>Parameters (JSON)</label>
              <textarea id="qcParams" placeholder='{"dept": "IT"}'>{}</textarea>
            </div>
            <div class="form-group">
              <label>Parameter Reference</label>
              <div class="qc-params-info" id="qcParamsInfo">
                <p class="text-muted text-sm">Select a query to see parameter details.</p>
              </div>
            </div>
          </div>
```

- [ ] **Step 5: Add the render function and wire it into query selection**

In `app/admin/js/app.js`, find `qcOnSelectQuery`:

```js
window.qcOnSelectQuery = function() {
  const sel = document.getElementById('qcName');
  const paramsEl = document.getElementById('qcParams');
  if (!sel || !paramsEl) return;
  const def = qcDefs.find(d => d.name === sel.value);
  const example = def ? qcExampleParams(def.params) : {};
  paramsEl.value = JSON.stringify(example, null, 2);
  refreshQcPreviews();
};
```

Replace with (adds `qcRenderParamsInfo` above it, and one new call inside):

```js
// Render the "Parameter Reference" panel next to Parameters (JSON) from the
// selected query's declared params — always the full list, independent of
// whatever the user has typed into the JSON textarea.
function qcRenderParamsInfo(def) {
  const el = document.getElementById('qcParamsInfo');
  if (!el) return;
  const entries = def ? Object.entries(def.params || {}) : [];
  if (entries.length === 0) {
    el.innerHTML = `<p class="text-muted text-sm">${
      def ? 'This query has no parameters.' : 'Select a query to see parameter details.'
    }</p>`;
    return;
  }
  el.innerHTML = entries.map(([name, p]) => `
    <div class="qc-param-item">
      <span class="qc-param-name">${esc(name)}</span>
      <span class="qc-param-meta">${esc(p.type || 'string')} &middot; ${p.required ? 'required' : 'optional'}</span>
      ${p.description ? `<div class="qc-param-desc">${esc(p.description)}</div>` : ''}
    </div>`).join('');
}

window.qcOnSelectQuery = function() {
  const sel = document.getElementById('qcName');
  const paramsEl = document.getElementById('qcParams');
  if (!sel || !paramsEl) return;
  const def = qcDefs.find(d => d.name === sel.value);
  const example = def ? qcExampleParams(def.params) : {};
  paramsEl.value = JSON.stringify(example, null, 2);
  qcRenderParamsInfo(def);
  refreshQcPreviews();
};
```

- [ ] **Step 6: Clear the panel when the database is switched**

In the `dbSelect` change handler (`app/admin/js/app.js`), find:

```js
      qcLoadDefinitions().then(() => {
        qcPopulatePicker();
        const nameEl = document.getElementById('qcName');
        const paramsEl = document.getElementById('qcParams');
        if (nameEl) nameEl.value = '';
        if (paramsEl) paramsEl.value = '{}';
        refreshQcPreviews();
      });
```

Replace with:

```js
      qcLoadDefinitions().then(() => {
        qcPopulatePicker();
        const nameEl = document.getElementById('qcName');
        const paramsEl = document.getElementById('qcParams');
        if (nameEl) nameEl.value = '';
        if (paramsEl) paramsEl.value = '{}';
        qcRenderParamsInfo(null);
        refreshQcPreviews();
      });
```

Without this, switching databases would leave the previous query's parameter descriptions on screen even though the query selection was cleared.

- [ ] **Step 7: Add the CSS**

In `app/admin/css/style.css`, after the existing Query Console block (ends at line 245, right before `.result-area { margin-top: 16px; }`), insert:

```css
.qc-params-info {
  border: 1px solid var(--gray-200); border-radius: var(--radius);
  padding: 10px 12px; min-height: 120px; max-height: 200px; overflow-y: auto;
  font-size: .85rem;
}
.qc-param-item { padding: 6px 0; border-bottom: 1px solid var(--gray-100); }
.qc-param-item:last-child { border-bottom: none; }
.qc-param-name { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-weight: 600; }
.qc-param-meta { color: var(--gray-500); font-size: .78rem; margin-left: 6px; }
.qc-param-desc { color: var(--gray-600); margin-top: 2px; }
```

- [ ] **Step 8: Run the tests again to verify they pass**

Run: `cd tests/e2e && npx playwright test query-console.spec.js -g "Parameter Reference"`
Expected: PASS (4 tests).

- [ ] **Step 9: Run the full spec file**

Run: `cd tests/e2e && npx playwright test query-console.spec.js`
Expected: All tests PASS (10 tests total: 6 pre-existing + 1 from Task 1 + 4 new).

- [ ] **Step 10: Commit**

```bash
git add app/admin/js/app.js app/admin/css/style.css tests/e2e/global-setup.js tests/e2e/query-console.spec.js
git commit -m "feat(admin-ui): add Parameter Reference panel to Query Console"
```

---

### Task 3: Apply the copy button to Procedure Console, Write Console, and Tasks

**Files:**
- Modify: `app/admin/js/app.js:1748-1757` (`executeProcedure`)
- Modify: `app/admin/js/app.js:1822-1831` (`executeWrite`)
- Modify: `app/admin/js/app.js:1869-1882` (`checkTask`)
- Create: `tests/e2e/raw-json-copy.spec.js`

**Interfaces:**
- Consumes: `rawJsonBlock(id, data)` from Task 1 — no changes to its signature.

- [ ] **Step 1: Write the failing tests**

Create `tests/e2e/raw-json-copy.spec.js`:

```js
const { test, expect } = require('@playwright/test');

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPass', 'changeme');
  await page.click('button:has-text("Login")');
  await expect(page.locator('#dbSelect')).toBeVisible();
}

test.beforeEach(async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await login(page);
});

test('Procedure Console: copying the raw JSON result works', async ({ page }) => {
  const mockResult = { code: 0, out_params: { p_salary: 5000 } };
  await page.route('**/api/v1/default/procedures/execute', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(mockResult),
  }));

  await page.click('.sidebar-nav a[data-page="procedure-console"]');
  await page.fill('#pcName', 'PKG_TEST.GET_INFO');
  await page.click('button:has-text("Execute")');
  await page.click('#pcResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});

test('Write Console: copying the raw JSON result works', async ({ page }) => {
  const mockResult = { affected_rows: 1 };
  await page.route('**/api/v1/default/writes/execute', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(mockResult),
  }));

  await page.click('.sidebar-nav a[data-page="write-console"]');
  await page.fill('#wcTable', 'HR.EMPLOYEES');
  await page.click('button:has-text("Execute Write")');
  await page.click('#wcResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});

test('Tasks: copying the raw JSON result works', async ({ page }) => {
  const mockResult = { task_id: 'abc123', status: 'SUCCESS', result: { rows: 1 } };
  await page.route('**/api/v1/tasks/abc123', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(mockResult),
  }));

  await page.click('.sidebar-nav a[data-page="tasks"]');
  await page.fill('#taskIdInput', 'abc123');
  await page.click('button:has-text("Check")');
  await page.click('#taskResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd tests/e2e && npx playwright test raw-json-copy.spec.js`
Expected: All 3 tests FAIL — each `button:has-text("Copy")` click times out because no Copy button exists in these three consoles' result areas yet.

- [ ] **Step 3: Procedure Console**

In `executeProcedure`, find:

```js
    resultArea.innerHTML = `
      <div class="mb-4"><span class="badge badge-green">SUCCESS</span></div>
      <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
    `;
  } catch (err) {
    resultArea.innerHTML = `<div class="mt-2"><span class="badge badge-red">ERROR</span> <span class="text-sm">${esc(err.message)}</span></div>`;
  }
};

/* ── Write Console
```

Replace the `resultArea.innerHTML` assignment (keep the `catch` block and everything after untouched) with:

```js
    resultArea.innerHTML = `
      <div class="mb-4"><span class="badge badge-green">SUCCESS</span></div>
      ${rawJsonBlock('pcResultJson', result)}
    `;
```

- [ ] **Step 4: Write Console**

In `executeWrite`, find:

```js
    resultArea.innerHTML = `
      <div class="mb-4"><span class="badge badge-green">SUCCESS</span></div>
      <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
    `;
  } catch (err) {
    resultArea.innerHTML = `<div class="mt-2"><span class="badge badge-red">ERROR</span> <span class="text-sm">${esc(err.message)}</span></div>`;
  }
};

/* ── Tasks
```

Replace the `resultArea.innerHTML` assignment with:

```js
    resultArea.innerHTML = `
      <div class="mb-4"><span class="badge badge-green">SUCCESS</span></div>
      ${rawJsonBlock('wcResultJson', result)}
    `;
```

- [ ] **Step 5: Tasks**

In `checkTask`, find:

```js
    resultArea.innerHTML = `
      <div class="mb-4">
        <span class="badge ${statusBadge}">${esc(result.status)}</span>
        <span class="text-muted text-sm" style="margin-left:8px">Task: ${esc(result.task_id)}</span>
      </div>
      <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
    `;
```

Replace with:

```js
    resultArea.innerHTML = `
      <div class="mb-4">
        <span class="badge ${statusBadge}">${esc(result.status)}</span>
        <span class="text-muted text-sm" style="margin-left:8px">Task: ${esc(result.task_id)}</span>
      </div>
      ${rawJsonBlock('taskResultJson', result)}
    `;
```

- [ ] **Step 6: Run the tests again to verify they pass**

Run: `cd tests/e2e && npx playwright test raw-json-copy.spec.js`
Expected: All 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/admin/js/app.js tests/e2e/raw-json-copy.spec.js
git commit -m "feat(admin-ui): add copy button to Procedure/Write/Tasks raw JSON results"
```

---

### Task 4: Apply the copy button to Data Maintenance's batch result

**Files:**
- Modify: `app/admin/js/app.js:2132-2155` (`dmExecute`'s committed-result branch)
- Modify: `tests/e2e/raw-json-copy.spec.js` (append one more test)

**Interfaces:**
- Consumes: `rawJsonBlock(id, data)` from Task 1.

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/raw-json-copy.spec.js`:

```js
test('Data Maintenance: copying the batch result raw JSON works', async ({ page }) => {
  await page.route('**/api/v1/default/schemas/describe/TEST_TABLE', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      table: 'TEST_TABLE',
      column_count: 1,
      primary_keys: ['ID'],
      columns: [{ name: 'ID', data_type: 'NUMBER', data_length: null, data_precision: 10, data_scale: 0, nullable: false, is_pk: true }],
    }),
  }));
  const batchResult = { total_affected: 1, operation_count: 1, results: [{ index: 0, operation: 'insert', affected_rows: 1 }] };
  await page.route('**/api/v1/default/writes/batch', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(batchResult),
  }));

  await page.click('.sidebar-nav a[data-page="data-maintenance"]');
  await page.fill('#dmTable', 'TEST_TABLE');
  await page.click('button:has-text("Load Schema")');
  await page.click('button:has-text("+ Insert Row")');
  await page.fill('input[data-idx="0"][data-target="values"][data-col="ID"]', '1');
  await page.click('button:has-text("Execute (Transaction)")');
  await page.click('#dmResultArea summary');
  await page.click('#dmResultArea button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(batchResult);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd tests/e2e && npx playwright test raw-json-copy.spec.js -g "Data Maintenance"`
Expected: FAIL — the `<pre>` in the committed-result branch has no Copy button yet.

- [ ] **Step 3: Update the committed-result branch**

In `dmExecute`, find:

```js
            <details class="mt-4"><summary class="text-sm" style="cursor:pointer">Raw JSON</summary>
              <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
            </details>
```

Replace with:

```js
            <details class="mt-4"><summary class="text-sm" style="cursor:pointer">Raw JSON</summary>
              ${rawJsonBlock('dmBatchResultJson', result)}
            </details>
```

- [ ] **Step 4: Run it again to verify it passes**

Run: `cd tests/e2e && npx playwright test raw-json-copy.spec.js -g "Data Maintenance"`
Expected: PASS.

- [ ] **Step 5: Run the full e2e suite**

Run: `cd tests/e2e && npx playwright test`
Expected: All tests across every spec file PASS (existing suites + `query-console.spec.js`'s new tests + `raw-json-copy.spec.js`'s 4 tests).

- [ ] **Step 6: Lint check**

Run: `ruff check app/ tests/`
Expected: No errors — this plan touches no Python files, so this is a no-op confirmation that nothing else broke.

- [ ] **Step 7: Commit**

```bash
git add app/admin/js/app.js tests/e2e/raw-json-copy.spec.js
git commit -m "feat(admin-ui): add copy button to Data Maintenance batch raw JSON result"
```
