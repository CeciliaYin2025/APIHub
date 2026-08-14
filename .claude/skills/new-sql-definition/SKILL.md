---
name: new-sql-definition
description: Create a new SQL definition for the API Hub that satisfies both the REST API and the MCP tool catalog. Use when the user wants to add, author, or draft a SQL definition / query definition, expose a table or view as an API endpoint, or add a new MCP query tool.
---

# New SQL Definition

Author one SQL definition that works over **both** the REST API and the MCP tool
catalog, then optionally create it.

**What to build:** $ARGUMENTS

Most of the rules below are not enforced by the API. Breaking a naming rule
produces a definition that is healthy over REST yet **invisible to every LLM**,
with nothing but a log line to say so — which is why Phase 3 is not optional.

## Phase 1: Gather context before drafting

Never invent an alias, a group, or a column name. Collect the real ones first.

1. **Available `db_alias` values** — `app/database_definitions/databases.yaml`
   (the `alias:` keys), or `GET /api/v1/databases`.
2. **Existing definition names and groups** — needed for the collision check in
   Phase 3 and to reuse an existing `group_name` rather than inventing a
   near-duplicate. Either:
   - `GET /api/v1/{db}/queries/definitions` (superuser), or
   - query the platform store directly:
     `SELECT db_alias, name, group_name FROM api_hub_sql_definitions ORDER BY db_alias, name`
     (connection from `POSTGRES_*` in `.env` — this is the internal store, not
     the data source).
3. **Real column names and types of the source object** — `GET /api/v1/{db}/schemas/tables/{table}/columns`,
   or ask. Do not guess column names; if you must, say so explicitly in the output.

If the user has not said which `db_alias`, ask. Everything else can be inferred
or flagged as an assumption.

## Phase 2: Draft the definition

Target shape (`app/schemas/sql_definition.py::SqlDefinitionCreate`):

```json
{
  "name": "...",
  "db_alias": "...",
  "group_name": "...",
  "description": "...",
  "result_description": "...",
  "sql": "...",
  "permission": "query:<name>",
  "params": {"<p>": {"type": "string|number|date", "required": false, "description": "..."}},
  "order_by": [{"column": "...", "direction": "ASC"}]
}
```

### SQL rules — breaking these breaks execution

1. Bind variables are Oracle-style `:name` **only**, never string interpolation.
   The dialect layer rewrites them to `%(name)s` for PostgreSQL/MSSQL, so the
   definition stays engine-agnostic.
2. **No trailing semicolon, and no `ORDER BY` inside the SQL.** The Hub wraps
   the SQL as a subquery — `SELECT ... FROM (<your sql>) sub_` — in every fetch
   mode, and an inline `ORDER BY` is illegal there on MSSQL. Sorting goes in the
   `order_by` field, which the Hub applies in its own wrapper.
   **One exception**: MSSQL permits `ORDER BY` in a derived table when it
   accompanies `TOP`, and `TOP` needs it to decide *which* rows to keep (see
   `eo_frozen_dates`). In that case keep the inner `ORDER BY` **and** still set
   `order_by` — the inner one only selects rows, the outer one orders output.
3. Every selected column needs a unique explicit name. `SELECT a.*, <expr>` is
   fine only if the expression is aliased and no output name repeats.
3a. **No CTEs.** `WITH ... SELECT` is illegal inside the subquery the Hub wraps
   every definition in, so a CTE fails at execution on MSSQL no matter how the
   definition validates. Use nested derived tables instead (see
   `eo_item_history`, which stacks GROUP BY → window → compare in three levels).
   On MSSQL note also that `STRING_AGG(DISTINCT ...)` is not supported even on
   2022 — pre-DISTINCT in an inner derived table, or use MIN + COUNT(DISTINCT).
4. Declared params and `:binds` in the SQL must match exactly, in both
   directions. An undeclared bind is a runtime error.
5. Optional filter pattern: `(:p IS NULL OR col = :p)`.

### Naming rules — breaking these silently hide the tool from every LLM

6. The MCP tool name is **derived, not chosen** (`mcp_server/naming.py`):
   `query_<db_alias>_<name>`, lowercased, every character outside
   `[A-Za-z0-9_-]` replaced by `_`, then hard-truncated to 64 chars.
7. Therefore **`len(db_alias) + len(name) <= 57`**. Past that the name is
   truncated: still callable, but unreadable to the model and liable to collide
   with any other long name sharing its prefix.
8. Names differing only in case or punctuation collide after sanitising —
   `HR_Emps`, `hr-emps`, and `HR.Emps` all become `hr_emps`. On collision the
   **first definition loaded wins and the other is dropped silently**
   (`mcp_server/catalog.py`). The name must be unique in lowercase across
   **all** aliases, not just its own.

### Text the model actually reads

9.  `description` is the tool-**selection** text: what business question this
    answers, and when to pick it over a similar definition. One or two
    sentences. It renders as `[<db_alias> · <group_name>] <description>`.
10. `result_description` defaults to `""` but **always write it** — it becomes
    the `Returns:` line listing the output columns, with units/currency where
    relevant. Without it a model has to run the query once just to learn the
    result shape.
11. `group_name` (max 100 chars) appears in that header and groups the Admin UI
    list. Reuse an existing group from Phase 1.
12. Each param `description` becomes its JSON Schema description — say what the
    filter does **and** what omitting it means. For `date` params the server
    appends `Format: YYYY-MM-DD` automatically; do not repeat it.

### Types and limits

13. `params[].type` is exactly `string`, `number`, or `date`. There is no
    boolean, array, or datetime. Comma-separated multi-value filters are
    `string` — say so in the description.
14. `order_by[].column` must match `^[A-Za-z_][A-Za-z0-9_]{0,127}$` — no dots,
    brackets, or expressions — and must be an **output** column of the SQL.
15. **Always set `order_by`.** Without a deterministic sort, pagination returns
    unstable pages; on MSSQL the wrapper falls back to `ORDER BY (SELECT NULL)`.
16. Over MCP the model may only use `fetch_mode` `page` (page_size <= 100),
    `count`, and `one` — `all` and `batch` are withheld because a six-figure
    result set does not belong in a context window. Design so **one page is
    useful**: prefer a summary shape over a 200-column dump. Bulk extraction is
    `scripts/extract_query.py`, not an MCP tool.
17. `permission` defaults to `query:<name>` when left empty. The MCP catalog is
    fetched with `permitted_only=true`, so a caller lacking that permission
    never sees the tool at all.

## Phase 3: Self-verify before presenting

Work through these and report each result. Do not skip one because it "looks
obviously fine" — rules 7 and 8 fail silently in production.

- [ ] **Rule 7 arithmetic**: show `len(db_alias) + len(name) = N <= 57`, and
      print the derived tool name `query_<alias>_<name>` in its final lowercase
      sanitised form.
- [ ] **Rule 8 collision**: sanitise the new tool name and compare against every
      existing definition's derived name from Phase 1, across all aliases. State
      what it was compared against.
- [ ] **Rule 4 binds**: list the `:binds` found in the SQL and the `params` keys
      side by side; they must be the same set.
- [ ] **Rule 2**: confirm no trailing `;` and no inline `ORDER BY`.
- [ ] **Rule 14**: confirm each `order_by.column` is an output column of the SQL
      and matches the identifier regex.
- [ ] **Guesses**: list any column, table, or filter semantics that was assumed
      rather than verified in Phase 1.

## Phase 4: Create it

Present the JSON first and let the user confirm the target environment. Creating
against PRD or test is a production write — do not do it unprompted.

```bash
curl -sS -X POST "$APIHUB_URL/api/v1/{db}/queries/definitions" \
  -H "X-API-Key: $APIHUB_API_KEY" -H "Content-Type: application/json" \
  -d @definition.json
```

Superuser only. On success the endpoint calls `sql_registry.reload_from_db()`
itself, so **no manual reload is needed** — unlike a change written straight to
the database, which does need one.

Then confirm the tool actually reached the catalog, since Phase 3 checks
predictions and this checks reality:

```bash
curl -sS "$APIHUB_URL/api/v1/mcp/tools" -H "X-API-Key: $APIHUB_API_KEY"
```

Find the new row and check `exposed: true`, `truncated: false`,
`shadowed_by: null`. The Admin UI **MCP Tools** page shows the same view. On the
MCP side no action is needed: the HTTP transport rebuilds on every `tools/list`,
and the stdio watcher polls the catalog fingerprint.

## Reference material

- Good models to copy: the PRD definitions `eo_detail` (params, `order_by`,
  wrapped function source) and `HR_SPMFG_Emps` (optional filters, column
  aliasing).
- **Do not copy `app/sql_definitions/*.yaml`** — those samples predate this
  design: they carry inline `ORDER BY` (rule 2) and have no `group_name` or
  `result_description`.
- `result_description` is empty on most existing rows because the column is
  recent; existing data is not a good model for rules 9-11.
