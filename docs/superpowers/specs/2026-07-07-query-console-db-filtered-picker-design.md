# Query Console — DB-filtered query picker with auto-filled example parameters

Date: 2026-07-07
Status: Approved (design)
Scope: Admin UI only — `app/admin/js/app.js` (+ minor `app/admin/css/style.css` if needed). No backend or data-model changes.

## Problem

The Query Console currently makes the operator do avoidable manual work:

- **Query Name** is a free-text `<input>` (`qcName`) — the operator must remember/type the exact definition name.
- **Parameters** is a JSON textarea defaulting to `{}` — the operator must know each parameter's name and type and hand-type the JSON.
- Nothing connects a chosen query to its parameters, so mistakes (wrong name, missing param, wrong type) surface only at execute time.

Goal: when the operator picks a **known** query, the console should present a ready-to-run form — the query is chosen from a list scoped to the current database, and its parameters are pre-filled with type-appropriate example values that the operator can then edit before executing.

## Decisions (from brainstorming)

1. **Example values are auto-generated from parameter type** — no `example` field is added to the data model. Mapping: `string → "example"`, `number → 0`, `date → "2024-01-01"`.
2. **The query picker is filtered by the top-bar DB selector** — the dropdown lists only the queries belonging to the currently selected database (`currentDb`). This follows the existing admin-UI convention where the top DB selector is the primary scope.
3. **Switching the top DB clears the selected query and its parameters** — stale selections/params from another database must not linger.
4. **`db` is carried into the request implicitly** — execute and the previews already target `currentDb` via `dbPrefix()`; the request URL/body therefore already reflect the correct database. No query→db reverse-switching.

## Data flow

1. **Populate the picker.** When the Query Console renders (and whenever it needs a refresh), call the existing endpoint `GET /{currentDb}/queries/definitions`. It returns each definition's `name`, `db_alias`, `description`, `permission`, and `params` (each param carrying `type`, `required`, `description`). Cache the result in a module-level variable for the current db.
2. **Query Name becomes a `<select>`** (replacing the free-text input), populated with one option per definition of `currentDb`, plus a leading empty placeholder option ("— select a query —"). No optgroups (single db only).
3. **On selection (`qcOnSelectQuery`)**: look up the selected definition's `params`, build an example object via `qcExampleParams(params)`, write it (pretty-printed) into the `qcParams` textarea, then call `refreshQcPreviews()`.
4. **On top-DB change**: if the Query Console is the active page, re-populate the picker from the newly selected db and **clear** the current selection and the `qcParams` textarea (reset to `{}`), then refresh previews.
5. **`prefillQuery(name)`** (invoked by "Open in Query Console" on a SQL Definition detail, which is already scoped to `currentDb`): navigate to the console, ensure the picker is populated, select the matching option, and run `qcOnSelectQuery` so parameters auto-fill.

## Components (all in `app/admin/js/app.js`)

- **`qcExampleParams(params)`** — pure helper. Given the `params` map (insertion order preserved), return `{ paramName: exampleValue }` where `exampleValue` is derived from `param.type`: `string → "example"`, `number → 0`, `date → "2024-01-01"`, unknown/missing → `"example"`.
- **`qcLoadDefinitions()`** — `async`. Fetch `GET /{currentDb}/queries/definitions`, store into a module var (e.g. `qcDefs`). On error, set `qcDefs = []` and surface a toast; the picker shows only the placeholder.
- **`renderQueryConsole()`** — change the Query Name field from `<input id="qcName">` to `<select id="qcName" onchange="qcOnSelectQuery()">`, then after injecting the HTML, `await qcLoadDefinitions()` and populate the options.
- **`qcOnSelectQuery()`** — global. Reads the selected definition (by name from `qcDefs`), fills `qcParams` with `qcExampleParams(def.params)`, refreshes previews. Selecting the empty placeholder option resets `qcParams` to `{}` and refreshes previews.
- **DB-selector change handler** (existing, `loadDatabases()` line ~110) — extend so that when the active page is `query-console`, it re-populates the picker and clears selection + params.
- **`qcBuildBody()`** — still reads the query name from `#qcName`; since it is now a `<select>`, `.value` is the definition name (option `value` = name). Placeholder option has `value=""`, producing an empty `query_name` (unchanged behavior when nothing is selected).
- **`prefillQuery(name)`** — set the select's value to `name`, then call `qcOnSelectQuery()` (after ensuring `qcLoadDefinitions()` has completed).

## Edge cases

- **No databases / empty definitions list** — picker shows only the placeholder; params textarea stays `{}`; Execute with an empty query name behaves as today (server rejects).
- **Switching DB with unsaved param edits** — intentionally discarded (decision 3): clear selection and reset params to `{}`.
- **Definition name uniqueness** — names are unique within a `db_alias` (registry key is `db_alias::name`), and the picker is single-db, so option `value = name` is unambiguous.
- **Fetch modes / page/batch/top_n fields** — unchanged; still independently editable after a query is picked.

## Non-goals (YAGNI)

- No `example` field added to `ParamDef` / `ParamDefSchema` / the DB.
- No cross-database query catalog and no query→db reverse switching.
- No type-ahead/search box on the picker (plain `<select>`); revisit only if a single db has an unwieldy number of definitions.

## Verification

The admin SPA has no JS test harness; verification is manual in the browser:

1. Select a database in the top bar → the Query Name dropdown lists only that db's queries.
2. Pick a query → Parameters auto-fills with a JSON object of that query's params using type-based example values; the Request Body and Python previews update accordingly.
3. Switch the top DB → the picker re-lists the new db's queries and the selection + parameters are cleared.
4. From a SQL Definition detail, click "Open in Query Console" → the query is pre-selected and its parameters auto-filled.
5. Edit an example value and Execute → the query runs against the selected db.
