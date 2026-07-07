# Query Console DB-filtered Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the Admin UI Query Console, replace the free-text Query Name input with a dropdown scoped to the currently selected database, and auto-fill the Parameters JSON with type-based example values when a query is picked.

**Architecture:** Pure front-end change in the vanilla-JS SPA (`app/admin/js/app.js`). A module-level cache holds the current db's definitions (fetched from the existing `GET /{currentDb}/queries/definitions`). Selecting a query fills the Parameters textarea from a pure `type → example` helper. Changing the top DB selector re-loads the picker and clears the selection.

**Tech Stack:** Vanilla JavaScript (no build step, no framework), served as static files from `app/admin/`. No JS test harness exists — verification is manual in Chrome, plus a reasoning check on the one pure helper.

## Global Constraints

- No backend or data-model changes. Do not add an `example` field to `ParamDef`/`ParamDefSchema`/DB.
- Example value mapping (verbatim): `string → "example"`, `number → 0`, `date → "2024-01-01"`, unknown/missing type → `"example"`.
- The query picker lists only definitions of the current top-bar database (`currentDb`).
- Switching the top DB clears the selected query and resets Parameters to `{}`.
- Preserve parameter insertion order when building the example object.
- Follow existing app.js conventions: global functions on `window` for inline `onclick`/`onchange`, module-scoped `let` vars near the top, `api('GET', path)` helper for requests, `toast(msg, type)` for user-facing errors.

---

### Task 1: Example-params helper and definition loader

**Files:**
- Modify: `app/admin/js/app.js` (add helpers + a module-level cache var near the other Query Console code, around line 1055–1064)

**Interfaces:**
- Produces:
  - `let qcDefs = [];` — module-level array of the current db's definitions, each `{ name, db_alias, description, permission, params }` where `params` is `{ [name]: { type, required, description } }`.
  - `qcExampleParams(params) -> object` — pure; maps each param to a type-based example value, preserving key order.
  - `async qcLoadDefinitions() -> void` — fetches `GET {dbPrefix()}/queries/definitions` into `qcDefs`; on error sets `qcDefs = []` and shows a toast.

- [ ] **Step 1: Add the module-level cache var**

Near the top-of-file module vars (where `currentDb`, `databases`, `token` are declared — search for `let currentDb`), add:

```javascript
let qcDefs = [];  // definitions for the Query Console picker (scoped to currentDb)
```

- [ ] **Step 2: Add the pure example-params helper**

Immediately above `function renderQueryConsole()` (currently line 1064), add:

```javascript
// Build an example parameter object from a definition's params map.
// Values are derived from each param's declared type so the operator gets a
// ready-to-edit JSON body. Key order is preserved.
function qcExampleParams(params) {
  const out = {};
  for (const [name, def] of Object.entries(params || {})) {
    const type = def && def.type;
    if (type === 'number') out[name] = 0;
    else if (type === 'date') out[name] = '2024-01-01';
    else out[name] = 'example';  // string + unknown/missing
  }
  return out;
}

// Load the current database's SQL definitions for the picker.
async function qcLoadDefinitions() {
  try {
    qcDefs = await api('GET', `${dbPrefix()}/queries/definitions`);
  } catch (e) {
    qcDefs = [];
    toast('Failed to load queries: ' + e.message, 'error');
  }
}
```

- [ ] **Step 3: Reasoning/console check of the pure helper**

`qcExampleParams` is pure. Verify by inspection against these cases (also runnable by pasting the function + call into the browser devtools console once the app is loaded):

```javascript
qcExampleParams({ dept: { type: 'string' }, n: { type: 'number' }, d: { type: 'date' }, x: {} })
// => { dept: "example", n: 0, d: "2024-01-01", x: "example" }
qcExampleParams({})   // => {}
qcExampleParams(null) // => {}
```

Confirm the returned object matches (order: dept, n, d, x).

- [ ] **Step 4: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat(admin): add Query Console example-params helper and definition loader"
```

---

### Task 2: Query Name dropdown + auto-fill on selection

**Files:**
- Modify: `app/admin/js/app.js` — `renderQueryConsole()` (currently lines 1065–1140), `qcBuildBody()` (line 1150–1170), `prefillQuery()` (lines 1056–1062); add `qcOnSelectQuery()` and `qcPopulatePicker()`.

**Interfaces:**
- Consumes: `qcDefs`, `qcExampleParams()`, `qcLoadDefinitions()` from Task 1; existing `refreshQcPreviews()`, `dbPrefix()`, `navigateTo()`.
- Produces:
  - `qcPopulatePicker() -> void` — fills `#qcName` options from `qcDefs` (placeholder first).
  - `window.qcOnSelectQuery = function() {...}` — on picker change, fills `#qcParams` with example JSON (or `{}` for the placeholder) and refreshes previews.
  - `window.prefillQuery = async function(name) {...}` — selects `name` in the picker and auto-fills.

- [ ] **Step 1: Replace the Query Name input with a select in `renderQueryConsole()`**

In the `qcTab-params` block, replace:

```html
            <div class="form-group">
              <label>Query Name</label>
              <input type="text" id="qcName" placeholder="e.g. employees">
            </div>
```

with:

```html
            <div class="form-group">
              <label>Query Name</label>
              <select id="qcName" onchange="qcOnSelectQuery()">
                <option value="">— select a query —</option>
              </select>
            </div>
```

- [ ] **Step 2: Load definitions and populate the picker after render**

At the very end of `renderQueryConsole()`, after the `area.innerHTML = ...` template assignment (after the closing backtick+semicolon at line ~1139), add:

```javascript
  qcLoadDefinitions().then(qcPopulatePicker);
```

- [ ] **Step 3: Add `qcPopulatePicker()` and `qcOnSelectQuery()`**

Immediately after `renderQueryConsole()` (before `window.toggleQcModeFields`), add:

```javascript
// Fill the Query Name picker from qcDefs, keeping a leading placeholder.
function qcPopulatePicker() {
  const sel = document.getElementById('qcName');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">— select a query —</option>' +
    qcDefs.map(d => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join('');
  // Restore selection if the name still exists in the new list.
  if (current && qcDefs.some(d => d.name === current)) sel.value = current;
}

// When a query is picked, fill Parameters with type-based examples.
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

- [ ] **Step 4: Update `prefillQuery()` to drive the picker**

Replace the whole `window.prefillQuery = function(name) {...}` (lines 1056–1062) with:

```javascript
window.prefillQuery = async function(name) {
  navigateTo('query-console');
  // renderQueryConsole kicks off an async load; wait for it, then select.
  await qcLoadDefinitions();
  qcPopulatePicker();
  const sel = document.getElementById('qcName');
  if (sel) {
    sel.value = name;
    qcOnSelectQuery();
  }
};
```

- [ ] **Step 5: Confirm `qcBuildBody()` still reads the name correctly**

`qcBuildBody()` reads `document.getElementById('qcName').value.trim()`. A `<select>` exposes `.value` (the selected option's value = the query name), so no change is required. Read lines 1150–1158 and confirm `const name = document.getElementById('qcName').value.trim();` is unchanged and correct. No edit in this step.

- [ ] **Step 6: Browser verification**

Load `/admin` (or `{root_path}/admin`), log in, go to Query Console. Verify:
- The Query Name field is a dropdown listing the current db's queries.
- Picking a query fills Parameters with a JSON object of that query's params using example values; the Request Body tab shows the matching `query_name` and `params`.
- Picking the placeholder resets Parameters to `{}`.
- From SQL Definitions → a definition → "Open in Query Console" pre-selects that query and fills its parameters.

- [ ] **Step 7: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat(admin): DB-scoped Query Name dropdown with auto-filled example params"
```

---

### Task 3: Re-load picker and clear selection on DB change

**Files:**
- Modify: `app/admin/js/app.js` — the `dbSelect` change handler inside `loadDatabases()` (lines 110–113).

**Interfaces:**
- Consumes: `qcLoadDefinitions()`, `qcPopulatePicker()` from Tasks 1–2; existing global that tracks the active page.

**Note:** The active page is tracked by the sidebar link with class `active` (set in `navigateTo`). Detect the Query Console being active via `document.querySelector('.sidebar-nav a[data-page="query-console"].active')`.

- [ ] **Step 1: Extend the DB-selector change handler**

Replace:

```javascript
  sel.addEventListener('change', () => {
    currentDb = sel.value;
    localStorage.setItem('hub_current_db', currentDb);
  });
```

with:

```javascript
  sel.addEventListener('change', () => {
    currentDb = sel.value;
    localStorage.setItem('hub_current_db', currentDb);
    // If the Query Console is open, re-scope its picker to the new db and
    // clear the stale selection + parameters.
    if (document.querySelector('.sidebar-nav a[data-page="query-console"].active')) {
      qcLoadDefinitions().then(() => {
        qcPopulatePicker();
        const nameEl = document.getElementById('qcName');
        const paramsEl = document.getElementById('qcParams');
        if (nameEl) nameEl.value = '';
        if (paramsEl) paramsEl.value = '{}';
        refreshQcPreviews();
      });
    }
  });
```

- [ ] **Step 2: Browser verification**

On the Query Console, switch the top DB selector. Verify:
- The Query Name dropdown re-lists only the newly selected db's queries.
- The previously selected query is cleared (placeholder shown) and Parameters resets to `{}`.
- The Request Body preview updates to the empty state.

- [ ] **Step 3: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat(admin): clear and reload Query Console picker when DB changes"
```

---

## Self-Review

**Spec coverage:**
- Decision 1 (type-based examples) → Task 1 `qcExampleParams` (mapping matches spec verbatim). ✓
- Decision 2 (picker filtered by top DB) → Task 1 `qcLoadDefinitions` uses `dbPrefix()`; Task 2 `qcPopulatePicker`. ✓
- Decision 3 (switching DB clears selection + params) → Task 3. ✓
- Decision 4 (db carried implicitly via `dbPrefix()`) → unchanged execute/preview path; no reverse switch. ✓
- Data-flow step 5 (`prefillQuery`) → Task 2 Step 4. ✓
- Edge case: empty placeholder resets params to `{}` → Task 2 `qcOnSelectQuery` (`def` undefined → `{}`). ✓
- Edge case: no databases / empty list → picker shows only placeholder (Task 2 Step 3 with empty `qcDefs`). ✓
- Verification steps → Task 2 Step 6, Task 3 Step 2 (manual browser). ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `qcDefs` (array of `{name, params, ...}`), `qcExampleParams(params)`, `qcLoadDefinitions()`, `qcPopulatePicker()`, `qcOnSelectQuery()`, `prefillQuery(name)` used consistently across tasks. Option `value` = definition `name`; `qcBuildBody()` reads `#qcName`.value = name (unchanged). ✓
