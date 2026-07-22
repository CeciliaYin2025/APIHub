# Query `count` Fetch Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `count` fetch mode to `POST /{db}/queries/execute` that returns only the matching row count (via the existing `COUNT(*)` wrapper query), without running the data query.

**Architecture:** Extend the existing single-endpoint, mode-dispatch design in `app/services/query_service.py`. `execute_query()` already dispatches on a `fetch_mode` string to mode-specific private functions (`_fetch_page`, `_fetch_all`, etc.); add `_fetch_count()` alongside them, reusing `Dialect.wrap_count()` and the existing `_get_count()` helper — no new SQL-generation code. Loosen the `QueryRequest.fetch_mode` regex to accept `"count"`. No new endpoint, no new response schema, no Admin UI JS beyond adding one `<option>`.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, pytest (existing patterns: `monkeypatch` for fakes, no live DB in unit tests).

## Global Constraints

- Parameterized queries only — `count` mode reuses `_prepare_binds`/`dialect.convert_bind_params`, never string-interpolates binds.
- Never branch on `db_type` inside services — dialect differences stay behind the `Dialect` interface (`wrap_count` is already engine-agnostic; no dialect changes needed).
- Line length 120, linted with ruff (`ruff check app/ tests/`).
- No new endpoint, no new Pydantic response model — the spec (`docs/superpowers/specs/2026-07-22-query-count-fetch-mode-design.md`) explicitly scopes this to extending the existing `/queries/execute` endpoint and `QueryRequest`/`QueryResponse` schemas.

---

### Task 1: Schema — accept `fetch_mode="count"`

**Files:**
- Modify: `app/schemas/query.py:8-33` (`QueryRequest`)
- Test: `tests/test_query_request_schema.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `QueryRequest(fetch_mode="count")` now validates successfully; `QueryRequest.fetch_mode` regex is `^(page|all|batch|top_n|one|count)$`. Later tasks (2, 3) rely on this validation already accepting `"count"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_query_request_schema.py`:

```python
"""Validation of QueryRequest.fetch_mode, including the count mode."""

import pytest
from pydantic import ValidationError

from app.schemas.query import QueryRequest


def test_count_fetch_mode_accepted():
    req = QueryRequest(query_name="q", fetch_mode="count")
    assert req.fetch_mode == "count"


def test_default_fetch_mode_is_page():
    req = QueryRequest(query_name="q")
    assert req.fetch_mode == "page"


@pytest.mark.parametrize("mode", ["page", "all", "batch", "top_n", "one", "count"])
def test_all_known_fetch_modes_accepted(mode):
    assert QueryRequest(query_name="q", fetch_mode=mode).fetch_mode == mode


def test_unknown_fetch_mode_rejected():
    with pytest.raises(ValidationError):
        QueryRequest(query_name="q", fetch_mode="bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_query_request_schema.py -v`
Expected: `test_count_fetch_mode_accepted` and `test_all_known_fetch_modes_accepted[count]` FAIL with a `pydantic.ValidationError` (pattern mismatch on `"count"`); the other tests PASS already.

- [ ] **Step 3: Update the schema**

In `app/schemas/query.py`, update `QueryRequest`:

```python
class QueryRequest(BaseModel):
    """Execute a named SQL query definition.

    Fetch modes (controlled by ``fetch_mode``):

    - **page** (default): paginated results, use ``page`` + ``page_size``.
    - **all**: return every row (no pagination). Use with caution on large datasets.
    - **batch**: stream results in a fixed-size batch, use ``batch_size`` + ``batch_offset``.
    - **top_n**: return the first N rows, use ``top_n``.
    - **one**: return exactly one row (or 404).
    - **count**: return only the matching row count (no data), via ``total``.
    """
    query_name: str = Field(..., description="Name of the registered SQL definition")
    params: dict[str, Any] = Field(default_factory=dict, description="Bind parameters")
    fetch_mode: str = Field(
        default="page",
        pattern="^(page|all|batch|top_n|one|count)$",
        description="Fetch mode: page, all, batch, top_n, one, count",
    )
```

(Only the docstring and the `fetch_mode` field's `pattern`/`description` change; every other field on `QueryRequest` is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_query_request_schema.py -v`
Expected: PASS (4 passed, 6 parametrized sub-cases included)

- [ ] **Step 5: Commit**

```bash
git add app/schemas/query.py tests/test_query_request_schema.py
git commit -m "feat(schema): accept fetch_mode=count on QueryRequest"
```

---

### Task 2: Service — `_fetch_count` and dispatch wiring

**Files:**
- Modify: `app/services/query_service.py:1-91` (module docstring + `execute_query` dispatch + new `_fetch_count`)
- Test: `tests/test_query_service_count.py` (new)

**Interfaces:**
- Consumes: `Dialect.wrap_count(sql: str) -> str` (existing, `app/db/dialect.py:42-44`); `_get_count(conn, count_sql, binds, dialect) -> int` (existing, `app/services/query_service.py:230-237`); `get_connection(db_alias)` / `get_dialect_for(db_alias)` (existing, imported at the top of `query_service.py`).
- Produces: `_fetch_count(sql: str, binds: dict[str, Any], db_alias: str | None = None) -> dict[str, Any]` returning `{"columns": [], "data": [], "total": int}`. `execute_query(..., fetch_mode="count")` returns this dict. Task 3 (API layer) relies on `execute_query` returning this exact shape for `"count"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_query_service_count.py`, following the existing fake-dialect/fake-connection pattern used in `tests/test_query_service_order_by.py`:

```python
"""execute_query(fetch_mode="count") returns only the row count, no data query."""

from app.services import query_service
from app.services.sql_registry import SqlDefinition


class _Dialect:
    """Records which wrap_* methods are called; wrap_count always available (base Dialect provides it)."""

    def __init__(self, count_value):
        self._count_value = count_value
        self.calls = []

    def wrap_count(self, sql):
        self.calls.append("wrap_count")
        return f"SELECT COUNT(*) FROM ({sql}) c"

    def wrap_paginated(self, sql, order_by=None):
        self.calls.append("wrap_paginated")
        return sql

    def wrap_all(self, sql, order_by=None):
        self.calls.append("wrap_all")
        return sql

    def wrap_top_n(self, sql, order_by=None):
        self.calls.append("wrap_top_n")
        return sql

    def wrap_one(self, sql, order_by=None):
        self.calls.append("wrap_one")
        return sql

    def pagination_binds(self, offset, page_size):
        return {}

    def top_n_binds(self, n):
        return {}

    @property
    def pagination_column(self):
        return None

    def convert_bind_params(self, sql, binds):
        return sql, binds

    def convert_value(self, v):
        return v

    def _count_row(self):
        return (self._count_value,)


class _Cursor:
    def __init__(self, dialect):
        self._dialect = dialect
        self.executed_sql = []
        self.description = [("a",)]

    def execute(self, sql, binds=None):
        self.executed_sql.append(sql)

    def fetchone(self):
        return self._dialect._count_row()

    def close(self):
        pass

    def __iter__(self):
        return iter([])


class _Conn:
    def __init__(self, dialect):
        self._dialect = dialect
        self.cursors = []

    def cursor(self):
        c = _Cursor(self._dialect)
        self.cursors.append(c)
        return c

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, dialect, conn):
    monkeypatch.setattr(query_service, "get_dialect_for", lambda alias: dialect)
    monkeypatch.setattr(query_service, "get_connection", lambda alias: conn)


def _defn():
    return SqlDefinition(name="q", description="", sql="SELECT a FROM t", permission="query:q", order_by=[])


def test_count_mode_returns_total_only(monkeypatch):
    dialect = _Dialect(count_value=4821)
    conn = _Conn(dialect)
    _patch(monkeypatch, dialect, conn)

    result = query_service.execute_query(_defn(), {}, fetch_mode="count")

    assert result == {"columns": [], "data": [], "total": 4821}


def test_count_mode_only_calls_wrap_count(monkeypatch):
    dialect = _Dialect(count_value=0)
    conn = _Conn(dialect)
    _patch(monkeypatch, dialect, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="count")

    assert dialect.calls == ["wrap_count"]


def test_count_mode_runs_exactly_one_query(monkeypatch):
    dialect = _Dialect(count_value=7)
    conn = _Conn(dialect)
    _patch(monkeypatch, dialect, conn)

    query_service.execute_query(_defn(), {}, fetch_mode="count")

    assert len(conn.cursors) == 1
    assert len(conn.cursors[0].executed_sql) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_query_service_count.py -v`
Expected: FAIL with `ValueError: Unknown fetch_mode: count` (raised by `execute_query`'s `else` branch at `app/services/query_service.py:91`)

- [ ] **Step 3: Implement `_fetch_count` and wire it into the dispatch**

In `app/services/query_service.py`, update the module docstring (lines 1-9):

```python
"""Service layer for executing SQL queries via engine-agnostic pool and dialect.

Supports six fetch modes:
- page   : paginated via page + page_size (default)
- all    : return every row
- batch  : fixed-size batch with offset (for cursor-style iteration)
- top_n  : first N rows only
- one    : exactly one row
- count  : matching row count only, no data query
"""
```

Add the `count` branch to `execute_query`'s dispatch (`app/services/query_service.py:80-91`):

```python
    if fetch_mode == "page":
        return _fetch_page(sql, bind_params, page, page_size, db_alias, order_by)
    elif fetch_mode == "all":
        return _fetch_all(sql, bind_params, db_alias, order_by)
    elif fetch_mode == "batch":
        return _fetch_batch(sql, bind_params, batch_size, batch_offset, db_alias, order_by)
    elif fetch_mode == "top_n":
        return _fetch_top_n(sql, bind_params, top_n, db_alias, order_by)
    elif fetch_mode == "one":
        return _fetch_one(sql, bind_params, db_alias, order_by)
    elif fetch_mode == "count":
        return _fetch_count(sql, bind_params, db_alias)
    else:
        raise ValueError(f"Unknown fetch_mode: {fetch_mode}")
```

Add `_fetch_count` immediately after `_fetch_one` (after line 224, before the `# ── helpers ──` section at line 227):

```python
def _fetch_count(
    sql: str,
    binds: dict[str, Any],
    db_alias: str | None = None,
) -> dict[str, Any]:
    """Return only the matching row count — no data query."""
    dialect = get_dialect_for(db_alias)
    count_sql = dialect.wrap_count(sql)

    with get_connection(db_alias) as conn:
        total = _get_count(conn, count_sql, binds, dialect)

    return {
        "columns": [],
        "data": [],
        "total": total,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_query_service_count.py -v`
Expected: PASS (3 passed)

Also run the full existing query-service suite to confirm no regression:

Run: `pytest tests/test_query_service.py tests/test_query_service_order_by.py -v`
Expected: PASS (all previously-passing tests still pass)

- [ ] **Step 5: Commit**

```bash
git add app/services/query_service.py tests/test_query_service_count.py
git commit -m "feat(query-service): add count fetch mode"
```

---

### Task 3: API docstring + Admin UI dropdown option

**Files:**
- Modify: `app/api/v1/queries.py:376-391` (`execute()` docstring only)
- Modify: `app/admin/js/app.js:1404-1410` (`#qcMode` select options)

**Interfaces:**
- Consumes: `execute_query(..., fetch_mode="count")` from Task 2 (already wired; the router passes `payload.fetch_mode` through unchanged, so no router logic changes).
- Produces: nothing new consumed by later tasks — this is the final task.

- [ ] **Step 1: Update the router docstring**

In `app/api/v1/queries.py`, update the `execute()` function's docstring (lines 383-391):

```python
    """Execute a registered SQL query against the specified database.

    Supports multiple fetch modes:
    - **page**: paginated (default). Use ``page`` + ``page_size``.
    - **all**: return all rows.
    - **batch**: fixed-size batch with offset. Use ``batch_size`` + ``batch_offset``.
    - **top_n**: first N rows. Use ``top_n``.
    - **one**: exactly one row (404 if none).
    - **count**: matching row count only (``total``), no data returned.
    """
```

No other change in this file — `execute()` already calls `execute_query(..., payload.fetch_mode, ...)` generically and builds `QueryResponse` from `result["total"]`/`result["columns"]`/`result["data"]`, all of which `_fetch_count` already supplies.

- [ ] **Step 2: Add the `count` option to the Query Console mode dropdown**

In `app/admin/js/app.js`, update the `#qcMode` select (around line 1404-1410):

```javascript
            <div class="form-group">
              <label>Fetch Mode</label>
              <select id="qcMode" onchange="toggleQcModeFields()">
                <option value="page">page</option>
                <option value="all">all</option>
                <option value="batch">batch</option>
                <option value="top_n">top_n</option>
                <option value="one">one</option>
                <option value="count">count</option>
              </select>
            </div>
```

No other JS change: `toggleQcModeFields()` (`app/admin/js/app.js:1490-1495`) only toggles the `page`/`batch`/`top_n` field rows and already hides all three for any other mode value; `qcBuildBody()` (`app/admin/js/app.js:1498-1518`) already sends just `{query_name, params, fetch_mode}` for modes other than `page`/`batch`/`top_n`.

- [ ] **Step 3: Verify with the existing service/schema test suite (no new automated test — this task is docstring + static HTML option only)**

Run: `pytest tests/test_query_request_schema.py tests/test_query_service_count.py tests/test_query_service.py tests/test_query_service_order_by.py -v`
Expected: PASS (all tests from Tasks 1-2 plus pre-existing tests still pass; this task adds no new Python logic to test)

- [ ] **Step 4: Manual verification in the Admin UI**

1. Start the app: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. Open `http://localhost:8000/admin`, log in, navigate to **Query Console**.
3. Pick any database and any query from the **Query Name** dropdown.
4. In **Fetch Mode**, select `count`. Confirm no page/batch/top_n input fields are shown.
5. Click **Execute Query**. Confirm the result area shows `Total: N rows | Returned: 0 rows` and `No data returned`, with `SUCCESS` badge and a `Raw JSON Response` panel showing `"columns": [], "data": []`.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/queries.py app/admin/js/app.js
git commit -m "docs(api): document count fetch mode; feat(admin-ui): add count option to Query Console"
```

---

## Self-Review Notes

- **Spec coverage:** API contract (Task 1 + 2), service implementation (Task 2), router docstring (Task 3 step 1), Admin UI dropdown (Task 3 step 2), no MCP changes (confirmed out of scope, untouched), no new endpoint/schema (confirmed — only `QueryRequest.fetch_mode` pattern changes), testing (unit tests in Tasks 1-2, manual UI check in Task 3) — all spec sections have a corresponding task.
- **Placeholder scan:** no TBD/TODO; every step has complete, runnable code.
- **Type consistency:** `_fetch_count(sql: str, binds: dict[str, Any], db_alias: str | None = None) -> dict[str, Any]` matches the signature style of `_fetch_one`/`_fetch_top_n` immediately above it in the file; return shape `{"columns": [], "data": [], "total": int}` matches every other `_fetch_*` function's return shape (all mode functions return the same three-key-minimum dict consumed by `QueryResponse` in `app/api/v1/queries.py:420-431`).
