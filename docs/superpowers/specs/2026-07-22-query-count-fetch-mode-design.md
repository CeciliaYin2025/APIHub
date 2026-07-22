# Query execute — `count` fetch mode

Date: 2026-07-22
Status: Approved (design)
Scope: `app/schemas/query.py`, `app/services/query_service.py`, `app/api/v1/queries.py` (docstring only), `app/admin/js/app.js`. No new endpoint, no data-model changes.

## Problem

`POST /{db}/queries/execute` supports five fetch modes (`page`, `all`, `batch`, `top_n`, `one`). `page`/`all`/`batch` already run a `COUNT(*)` wrapper query internally to populate the response's `total` field, but only *after* also running the full data query — a caller has no way to find out how many rows a query+params combination matches without either running a full fetch or hand-rolling their own count query outside the API.

This matters for both the Admin UI Query Console and external API/MCP-adjacent callers: knowing the row count up front lets an operator choose the right fetch strategy (e.g. "12 rows, just use `all`" vs. "480,000 rows, page through it or add tighter filters") without pulling data first.

## Decision (from brainstorming)

Add `count` as a sixth `fetch_mode` value on the **existing** `POST /{db}/queries/execute` endpoint, rather than a new dedicated endpoint (e.g. `/queries/count`). Rationale: it reuses the existing request shape (`query_name` + `params`), existing permission check, and existing `COUNT(*)` wrapping logic (`Dialect.wrap_count` / `_get_count`) with no new router surface or schema duplication — consistent with how the other four modes already share the one endpoint.

## API contract

```
POST /{db}/queries/execute
{ "query_name": "list_employees", "params": {"dept": "IT"}, "fetch_mode": "count" }

→ 200 OK
{ "success": true, "query_name": "list_employees", "fetch_mode": "count",
  "total": 4821, "columns": [], "data": [] }
```

- No new fields on `QueryRequest` — `count` takes no mode-specific parameters (no page/batch/top_n fields apply).
- `QueryResponse` shape is unchanged; `columns` and `data` are simply empty since no data query runs.

## Implementation

- **`app/schemas/query.py`** — `QueryRequest.fetch_mode` pattern becomes `^(page|all|batch|top_n|one|count)$`; update the docstring's mode list.
- **`app/services/query_service.py`**:
  - Add `_fetch_count(sql, binds, db_alias=None)`:
    ```python
    def _fetch_count(sql, binds, db_alias=None):
        dialect = get_dialect_for(db_alias)
        count_sql = dialect.wrap_count(sql)
        with get_connection(db_alias) as conn:
            total = _get_count(conn, count_sql, binds, dialect)
        return {"columns": [], "data": [], "total": total}
    ```
  - Wire into `execute_query`'s dispatch: `elif fetch_mode == "count": return _fetch_count(sql, bind_params, db_alias)`.
  - Update the module docstring's mode list (currently lines 3-8) to add `count`.
- **`app/api/v1/queries.py`** — no logic change; `execute()` already passes `fetch_mode` through generically. Extend the `execute()` docstring's mode bullet list to mention `count`.
- **Permissions** — unchanged. `count` still runs a real query against the data source, so it goes through the same `PermissionChecker(definition.permission, "read")` check as every other mode.
- **MCP server** (`mcp_server/`) — out of scope. `mcp_server/catalog.py` builds tool schemas that only ever call `fetch_mode=page` under the hood; it doesn't expose mode selection to MCP tool callers, so nothing there changes.

## Admin UI (Query Console)

In `app/admin/js/app.js`:

- Add `<option value="count">count</option>` to the `#qcMode` select (~line 1409, alongside `page`/`all`/`batch`/`top_n`/`one`).
- `toggleQcModeFields()` — no change needed. It only shows page/batch/top_n field rows when the mode matches those specific strings; `count` matches none, so all mode-specific rows correctly stay hidden.
- `qcBuildBody()` — no change needed. For any mode other than `page`/`batch`/`top_n` it already sends just `{ query_name, params, fetch_mode }`.
- Result rendering (`executeQuery()`) — no change needed. The existing result block already renders "Total: N rows | Returned: 0 rows" plus "No data returned" when `data` is empty, which reads correctly for a count-only result.

## Non-goals (YAGNI)

- No new `/queries/count` endpoint.
- No change to `one` mode's total semantics (still `total: 1` / 404-on-empty; it's a single-record fetch, not a listing mode this feature targets).
- No MCP server changes — MCP tools don't expose fetch-mode selection today.
- No special-cased UI rendering for `count` results — the existing generic result block already communicates it clearly.

## Testing

- **Unit** (`app/services/query_service.py`): `execute_query(..., fetch_mode="count")` returns `{"columns": [], "data": [], "total": N}` matching a known fixture/mock count, and does not invoke the data-fetch path.
- **API** (`app/api/v1/queries.py`): `POST /{db}/queries/execute` with `fetch_mode=count` returns 200 with the documented shape; an invalid `fetch_mode` value still returns 422 (pattern still enforced with the new value added).
- **Regression**: existing `page`/`all`/`batch`/`top_n`/`one` tests keep passing unmodified.
- **Manual** (Admin UI): open Query Console, pick a query, select `count` mode, Execute → result area shows `Total: N rows | Returned: 0 rows` and "No data returned", with no leftover page/batch/top_n input fields visible.
