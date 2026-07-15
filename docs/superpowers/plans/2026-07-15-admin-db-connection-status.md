# Admin Dashboard DB Connection Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a live, per-database connection status (Online / Offline / Timeout, with latency and driver error) in the Admin dashboard's Databases card.

**Architecture:** A new `health_check_sql()` method on the `Dialect` base class supplies the engine-correct probe query (`SELECT 1 FROM DUAL` for Oracle, `SELECT 1` elsewhere). A new `db_health_service` executes that probe per alias, fanning out across a bounded `ThreadPoolExecutor` under a single shared deadline (the DB drivers are blocking, so threads are the only way to parallelize). A superuser-only `GET /api/v1/databases/status` exposes the results, and the dashboard fetches it in a second, non-blocking pass so a slow database never delays page render.

**Tech Stack:** Python 3.10+, FastAPI, pytest, ruff. Admin UI is a vanilla-JS SPA (no build step, no framework).

**Spec:** `docs/superpowers/specs/2026-07-15-admin-db-connection-status-design.md`

## Global Constraints

- Python 3.10+, line length 120, linted with `ruff check app/ tests/`.
- **Never branch on `db_type` inside services** — engine differences belong in the `Dialect` base class (CLAUDE.md).
- Tests run with `pytest -v`; `testpaths = ["tests"]`. There is **no `tests/conftest.py`** — tests are self-contained and use `monkeypatch`.
- Endpoint tests in this repo **call route functions directly** and assert wiring via router introspection; they do not use `TestClient`.
- All strings interpolated into the Admin UI DOM must pass through the existing `esc()` helper.
- Status values are exactly the strings `"online"`, `"offline"`, `"timeout"`.
- The per-alias result dict shape is fixed across all tasks:
  `{"alias": str, "status": str, "latency_ms": float | None, "error": str | None, "checked_at": str}`

**Note on test file layout:** spec §8 lists a single `tests/test_db_health.py`. This plan splits dialect tests into `tests/test_dialect_health_check.py` to match the existing repo convention (`tests/test_dialect_order_by.py` vs `tests/test_registry_order_by.py`). Behaviour coverage is identical to spec §7.

---

### Task 1: Dialect health-check SQL

**Files:**
- Modify: `app/db/dialect.py` (append to the `Dialect` ABC, after `table_name_regex` at the end of the class)
- Modify: `app/db/dialects/oracle_dialect.py`
- Modify: `app/db/dialects/postgresql_dialect.py`
- Modify: `app/db/dialects/mssql_dialect.py`
- Test: `tests/test_dialect_health_check.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Dialect.health_check_sql(self) -> str` — returns a trivial query proving the connection is alive. Task 2 calls this via `get_dialect(db_type).health_check_sql()`.

**Why all four files change in one step:** `health_check_sql` is an `@abc.abstractmethod`. The moment it lands on the base class, any dialect lacking it cannot be instantiated, and the existing dialect test suite breaks. Base + all three implementations must therefore land together.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dialect_health_check.py`:

```python
"""Each dialect returns the engine-correct trivial connectivity probe."""

from app.db.dialects.mssql_dialect import MssqlDialect
from app.db.dialects.oracle_dialect import OracleDialect
from app.db.dialects.postgresql_dialect import PostgresqlDialect


def test_oracle_health_check_requires_dual():
    # Oracle has no bare SELECT — it needs a FROM clause.
    assert OracleDialect().health_check_sql() == "SELECT 1 FROM DUAL"


def test_postgresql_health_check_sql():
    assert PostgresqlDialect().health_check_sql() == "SELECT 1"


def test_mssql_health_check_sql():
    assert MssqlDialect().health_check_sql() == "SELECT 1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dialect_health_check.py -v`
Expected: FAIL — `AttributeError: 'OracleDialect' object has no attribute 'health_check_sql'`

- [ ] **Step 3: Add the abstract method to the base class**

In `app/db/dialect.py`, append at the very end of the `Dialect` class (after `table_name_regex`), matching the file's existing `# ── Section ──` comment style:

```python
    # ── Health check ────────────────────────────────────────────────────

    @abc.abstractmethod
    def health_check_sql(self) -> str:
        """Return a trivial query that proves the connection is alive."""
```

- [ ] **Step 4: Implement in all three dialects**

At the end of `OracleDialect` in `app/db/dialects/oracle_dialect.py`:

```python
    # ── Health check ────────────────────────────────────────────────────

    def health_check_sql(self) -> str:
        return "SELECT 1 FROM DUAL"
```

At the end of `PostgresqlDialect` in `app/db/dialects/postgresql_dialect.py`:

```python
    # ── Health check ────────────────────────────────────────────────────

    def health_check_sql(self) -> str:
        return "SELECT 1"
```

At the end of `MssqlDialect` in `app/db/dialects/mssql_dialect.py`:

```python
    # ── Health check ────────────────────────────────────────────────────

    def health_check_sql(self) -> str:
        return "SELECT 1"
```

- [ ] **Step 5: Run the new tests plus the whole suite**

Run: `pytest tests/test_dialect_health_check.py -v`
Expected: PASS (3 passed)

Run: `pytest -v`
Expected: PASS — no pre-existing test breaks. A failure here means a dialect is missing the method and can no longer be instantiated.

- [ ] **Step 6: Lint**

Run: `ruff check app/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add app/db/dialect.py app/db/dialects/ tests/test_dialect_health_check.py
git commit -m "feat(db): add health_check_sql() to the Dialect layer"
```

---

### Task 2: `check_database()` — single-alias probe

**Files:**
- Create: `app/services/db_health_service.py`
- Test: `tests/test_db_health.py`

**Interfaces:**
- Consumes: `Dialect.health_check_sql()` (Task 1); `db_registry.get_db_type(alias)` and `db_registry.get_cursor(alias)` from `app/db/multi_pool.py`; `get_dialect(db_type)` from `app/db/dialect_registry.py`.
- Produces: `check_database(alias: str) -> dict[str, Any]` returning the fixed result dict (see Global Constraints). Status is `"online"` or `"offline"` only — `"timeout"` is produced by Task 3, never here. Also produces the private helper `_now_iso() -> str` used by Task 3.

**Context the implementer needs:** `db_registry.get_cursor(alias)` is a `@contextmanager`. For an alias whose pool failed at startup it internally retries `init_pool`, which raises if the database is still unreachable — so a dead pool surfaces here as an ordinary exception, which is exactly what the `offline` branch captures. That is intentional, not an edge case to guard against.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_health.py`:

```python
"""Per-database connectivity probing."""

import contextlib

from app.services import db_health_service as svc


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, *args, **kwargs):
        self.executed.append(sql)

    def fetchone(self):
        return (1,)


def _patch_registry(monkeypatch, cursor_cm, db_type="oracle"):
    monkeypatch.setattr(svc.db_registry, "get_db_type", lambda alias: db_type)
    monkeypatch.setattr(svc.db_registry, "get_cursor", cursor_cm)


def test_check_database_online(monkeypatch):
    @contextlib.contextmanager
    def _ok(alias):
        yield _FakeCursor()

    _patch_registry(monkeypatch, _ok)
    result = svc.check_database("orcl")

    assert result["alias"] == "orcl"
    assert result["status"] == "online"
    assert result["error"] is None
    assert isinstance(result["latency_ms"], float)
    assert result["checked_at"]


def test_check_database_uses_dialect_sql(monkeypatch):
    cursor = _FakeCursor()

    @contextlib.contextmanager
    def _ok(alias):
        yield cursor

    _patch_registry(monkeypatch, _ok, db_type="oracle")
    svc.check_database("orcl")

    # Proves the probe goes through the dialect, not a hardcoded string.
    assert cursor.executed == ["SELECT 1 FROM DUAL"]


def test_check_database_offline_captures_driver_error(monkeypatch):
    @contextlib.contextmanager
    def _boom(alias):
        raise RuntimeError("ORA-12541: TNS:no listener")
        yield  # pragma: no cover

    _patch_registry(monkeypatch, _boom)
    result = svc.check_database("orcl")

    assert result["status"] == "offline"
    assert "ORA-12541" in result["error"]
    assert result["latency_ms"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.db_health_service'`

- [ ] **Step 3: Write the implementation**

Create `app/services/db_health_service.py`:

```python
"""Live connectivity checks for the configured data-source databases."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.db.dialect_registry import get_dialect
from app.db.multi_pool import db_registry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def check_database(alias: str) -> dict[str, Any]:
    """Probe *alias* with a trivial query and report its connectivity.

    Never raises: any driver/config failure is reported as ``offline`` so one
    dead data source cannot break the status of the others.
    """
    started = time.perf_counter()
    try:
        dialect = get_dialect(db_registry.get_db_type(alias))
        with db_registry.get_cursor(alias) as cursor:
            cursor.execute(dialect.health_check_sql())
            cursor.fetchone()
    except Exception as exc:
        # Includes the case where the pool failed at startup and init_pool
        # is retried (and re-fails) inside get_cursor.
        logger.warning("Health check failed for '%s': %s", alias, exc)
        return {
            "alias": alias,
            "status": "offline",
            "latency_ms": None,
            "error": str(exc),
            "checked_at": _now_iso(),
        }

    return {
        "alias": alias,
        "status": "online",
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": None,
        "checked_at": _now_iso(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db_health.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint**

Run: `ruff check app/ tests/`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add app/services/db_health_service.py tests/test_db_health.py
git commit -m "feat(health): add check_database() connectivity probe"
```

---

### Task 3: `check_all_databases()` — bounded fan-out with a shared deadline

**Files:**
- Modify: `app/core/config.py` (add one setting to the `Settings` class)
- Modify: `app/services/db_health_service.py`
- Test: `tests/test_db_health.py` (append)

**Interfaces:**
- Consumes: `check_database(alias)` and `_now_iso()` (Task 2); `db_registry.aliases()` from `app/db/multi_pool.py`; `settings.db_health_check_timeout`.
- Produces: `check_all_databases() -> list[dict[str, Any]]` — one result dict per configured alias. Task 4 calls this.

**The key design decision — read before implementing:** the timeout is a **single budget shared by all aliases**, not per-alias. Futures are collected one at a time, so a naive `future.result(timeout=N)` per future would let ten dead databases stall the request for 10×N seconds. Compute one `deadline` after submitting, then give each `future.result()` only the time *remaining* until that deadline.

**Known limitation (spec §6):** a blocked driver call cannot be interrupted — Python cannot safely kill a running thread. On timeout the worker thread is abandoned and lives until the driver's own network timeout fires. This bounds *the dashboard's wait*, not the thread's life. Two mitigations, both required: the executor is bounded at 8 workers so repeated refreshes against a hung database cannot grow threads without limit, and `shutdown(wait=False)` ensures the response returns without joining abandoned threads.

- [ ] **Step 1: Write the failing tests**

First, extend the **existing import block at the top** of `tests/test_db_health.py` so it reads:

```python
import contextlib
import threading
import time

from app.services import db_health_service as svc
```

Do not add these imports mid-file — ruff's default rule set includes E402
(module-level import not at top of file) and the lint step would fail.

Then append to `tests/test_db_health.py`:

```python
def _result(alias, status="online"):
    return {
        "alias": alias,
        "status": status,
        "latency_ms": 1.0 if status == "online" else None,
        "error": None if status == "online" else "boom",
        "checked_at": "2026-07-15T00:00:00+00:00",
    }


def test_check_all_returns_one_entry_per_alias(monkeypatch):
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["a", "b"])
    monkeypatch.setattr(svc, "check_database", lambda alias: _result(alias))

    results = svc.check_all_databases()

    assert sorted(r["alias"] for r in results) == ["a", "b"]


def test_check_all_with_no_aliases_returns_empty(monkeypatch):
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: [])
    assert svc.check_all_databases() == []


def test_check_all_isolates_failures(monkeypatch):
    def _fake(alias):
        return _result(alias, "offline" if alias == "bad" else "online")

    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["good", "bad"])
    monkeypatch.setattr(svc, "check_database", _fake)

    statuses = {r["alias"]: r["status"] for r in svc.check_all_databases()}

    # One dead database must not poison the others.
    assert statuses == {"good": "online", "bad": "offline"}


def test_check_all_marks_hung_database_timeout(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["slow"])
    monkeypatch.setattr(svc.settings, "db_health_check_timeout", 0.1)
    monkeypatch.setattr(svc, "check_database", lambda alias: release.wait(10))

    try:
        results = svc.check_all_databases()
        assert results[0]["status"] == "timeout"
        assert results[0]["latency_ms"] is None
        assert "exceeded" in results[0]["error"]
    finally:
        # Release the abandoned worker so it does not block interpreter exit.
        release.set()


def test_check_all_shares_one_deadline_across_aliases(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(svc.db_registry, "aliases", lambda: ["s1", "s2", "s3"])
    monkeypatch.setattr(svc.settings, "db_health_check_timeout", 0.2)
    monkeypatch.setattr(svc, "check_database", lambda alias: release.wait(10))

    try:
        started = time.perf_counter()
        results = svc.check_all_databases()
        elapsed = time.perf_counter() - started

        assert all(r["status"] == "timeout" for r in results)
        # Three hung databases share ONE 0.2s budget; they must not
        # accumulate to 3 x 0.2s.
        assert elapsed < 0.6
    finally:
        release.set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db_health.py -v`
Expected: FAIL — `AttributeError: module 'app.services.db_health_service' has no attribute 'check_all_databases'`

- [ ] **Step 3: Add the timeout setting**

In `app/core/config.py`, inside the `Settings` class, add below the `database_definitions_dir` line:

```python
    # Health check: seconds allowed for ALL database probes combined
    db_health_check_timeout: float = 5.0
```

- [ ] **Step 4: Implement the fan-out**

In `app/services/db_health_service.py`, extend the imports at the top:

```python
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from app.core.config import settings
```

Add below the module logger:

```python
# Bounded so repeated refreshes against a hung database cannot grow threads
# without limit (see spec section 6).
_MAX_WORKERS = 8
```

Append at the end of the file:

```python
def check_all_databases() -> list[dict[str, Any]]:
    """Probe every configured database concurrently under one shared deadline.

    The drivers are blocking, so concurrency requires threads. All aliases
    share a single ``db_health_check_timeout`` budget rather than each getting
    their own, so N dead databases cost N-times-nothing extra in wall clock.
    """
    aliases = db_registry.aliases()
    if not aliases:
        return []

    timeout = settings.db_health_check_timeout
    executor = ThreadPoolExecutor(max_workers=min(len(aliases), _MAX_WORKERS))
    try:
        futures = {alias: executor.submit(check_database, alias) for alias in aliases}
        deadline = time.perf_counter() + timeout

        results: list[dict[str, Any]] = []
        for alias, future in futures.items():
            remaining = max(0.0, deadline - time.perf_counter())
            try:
                results.append(future.result(timeout=remaining))
            except FutureTimeoutError:
                future.cancel()
                logger.warning("Health check for '%s' exceeded %.1fs", alias, timeout)
                results.append({
                    "alias": alias,
                    "status": "timeout",
                    "latency_ms": None,
                    "error": f"Health check exceeded {timeout}s",
                    "checked_at": _now_iso(),
                })
        return results
    finally:
        # Do not join abandoned workers — a hung driver call would block the
        # response until its own network timeout fires.
        executor.shutdown(wait=False)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_db_health.py -v`
Expected: PASS (8 passed — 3 from Task 2, 5 new)

- [ ] **Step 6: Lint**

Run: `ruff check app/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add app/core/config.py app/services/db_health_service.py tests/test_db_health.py
git commit -m "feat(health): check all databases concurrently under a shared deadline"
```

---

### Task 4: `GET /api/v1/databases/status` endpoint

**Files:**
- Modify: `app/api/v1/databases.py`
- Test: `tests/test_db_health_endpoint.py`

**Interfaces:**
- Consumes: `check_all_databases()` (Task 3); `get_current_active_superuser` from `app/services/auth_service.py:227`.
- Produces: `GET /api/v1/databases/status` → `list[dict]`; the route function `database_status(_admin)`.

**Context the implementer needs:**
- The router already exists with `prefix="/databases"` and currently holds only `GET ""`. There is **no `/{alias}` path param**, so `/status` cannot be shadowed by a catch-all route.
- Declare the function as `def`, **not** `async def`. FastAPI runs sync routes in its own threadpool; an `async def` here would block the event loop on the blocking driver calls.
- This endpoint is superuser-only because it opens real connections and returns raw driver errors — stricter than the sibling `GET ""`, which only needs `get_current_user`. Follow the existing repo idiom `_admin: User = Depends(get_current_active_superuser)` (see `app/api/v1/auth.py:85`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_health_endpoint.py`:

```python
"""GET /databases/status is registered, superuser-gated, and returns service output."""

from app.api.v1 import databases as db_mod
from app.services.auth_service import get_current_active_superuser


def _route(path):
    # APIRouter bakes its prefix into route.path, so the full path is
    # "/databases/status" even though the decorator says "/status".
    return next(r for r in db_mod.router.routes if r.path == path)


def test_status_route_registered():
    assert "GET" in _route("/databases/status").methods


def test_status_route_requires_superuser():
    deps = _route("/databases/status").dependant.dependencies
    # The check opens real connections and returns raw driver errors, so it
    # must be superuser-gated, not merely authenticated.
    assert any(d.call is get_current_active_superuser for d in deps)


def test_database_status_returns_service_results(monkeypatch):
    payload = [{"alias": "a", "status": "online", "latency_ms": 1.0, "error": None, "checked_at": "t"}]
    monkeypatch.setattr(db_mod, "check_all_databases", lambda: payload)

    assert db_mod.database_status(_admin=None) == payload
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db_health_endpoint.py -v`
Expected: FAIL — `StopIteration` in `_route` (no `/status` route exists yet)

- [ ] **Step 3: Implement the endpoint**

In `app/api/v1/databases.py`, update the auth import and add the service import:

```python
from app.services.auth_service import get_current_active_superuser, get_current_user
from app.services.db_health_service import check_all_databases
```

Append at the end of the file:

```python
@router.get("/status")
def database_status(_admin: User = Depends(get_current_active_superuser)):
    """Check live connectivity for every configured database.

    Superuser-only: this opens real connections and returns raw driver errors.
    """
    return check_all_databases()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db_health_endpoint.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the whole suite and lint**

Run: `pytest -v`
Expected: PASS — no regressions.

Run: `ruff check app/ tests/`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/databases.py tests/test_db_health_endpoint.py
git commit -m "feat(api): add superuser-only GET /databases/status"
```

---

### Task 5: Admin UI — Status column and Refresh button

**Files:**
- Modify: `app/admin/js/app.js` (the Databases card, currently lines 308-320, inside `renderDashboard()`)

**Interfaces:**
- Consumes: `GET /databases/status` (Task 4); existing globals `api(method, path)`, `esc(str)`, `databases`, `currentDb`.
- Produces: globals `refreshDbStatus()` and `dbStatusHtml(row)`.

**Context the implementer needs:**
- This is a vanilla-JS SPA — no build step, no framework, no imports. Functions are plain globals and buttons wire up with inline `onclick`, matching the existing file.
- `api()` **throws** on any non-2xx (including the 403 a non-superuser gets), so the catch branch is what renders `—` for non-superusers. The dashboard must stay fully usable for them.
- CSS classes `badge-green`, `badge-red`, `badge-amber`, `btn btn-sm` all already exist in `app/admin/css/`. Do not add CSS.
- Element IDs are built as `dbstat-${alias}`. This is safe without escaping because `DatabaseRegistry` enforces the alias regex `^[a-zA-Z][a-zA-Z0-9_]{0,63}$` (`app/db/multi_pool.py:27`). Driver error text is **not** trusted and must go through `esc()`.
- **Two-pass render is the whole point:** `renderDashboard()` must paint immediately with `Checking…` placeholders, and `refreshDbStatus()` must be called **without `await`** so a slow database never delays the rest of the page.

- [ ] **Step 1: Add the Status column and Refresh button**

In `app/admin/js/app.js`, replace the Databases card block (currently lines 308-320) with:

```js
      <div class="card mt-4">
        <div class="card-header">
          <h2>Databases (${databases.length})</h2>
          <button class="btn btn-sm" onclick="refreshDbStatus()">Refresh</button>
        </div>
        <div class="card-body no-pad">
          <table class="data-table"><thead><tr><th>Alias</th><th>Type</th><th>Description</th><th>Connection</th><th>User</th><th>Status</th></tr></thead>
          <tbody>${databases.map(d => `<tr>
            <td><strong>${esc(d.alias)}</strong>${d.alias === currentDb ? ' <span class="badge badge-blue">Active</span>' : ''}</td>
            <td><span class="badge ${d.db_type === 'oracle' ? 'badge-blue' : d.db_type === 'postgresql' ? 'badge-green' : 'badge-amber'}">${esc((d.db_type || 'oracle').toUpperCase())}</span></td>
            <td>${esc(d.description || '—')}</td>
            <td><code>${esc(d.dsn || (d.host ? d.host + ':' + d.port + '/' + d.database : '—'))}</code></td>
            <td>${esc(d.user)}</td>
            <td id="dbstat-${d.alias}">Checking…</td>
          </tr>`).join('')}</tbody></table>
        </div>
      </div>
```

- [ ] **Step 2: Kick off the status fetch after the dashboard paints**

Still in `renderDashboard()`, immediately after the closing backtick and semicolon of the `area.innerHTML = \`...\`;` assignment and **before** the `} catch (err) {` line, add:

```js
    // Deliberately NOT awaited: a slow or hung database must not delay the
    // rest of the dashboard from rendering.
    refreshDbStatus();
```

- [ ] **Step 3: Add the status helpers**

Add these two functions immediately after the end of the `renderDashboard()` function (after its closing `}`), before the next section comment:

```js
function dbStatusHtml(r) {
  if (r.status === 'online') {
    return `<span class="badge badge-green">Online</span> <small>${esc(String(r.latency_ms))}ms</small>`;
  }
  const badge = r.status === 'timeout'
    ? '<span class="badge badge-amber">Timeout</span>'
    : '<span class="badge badge-red">Offline</span>';
  const err = r.error || '';
  const short = err.length > 60 ? err.slice(0, 60) + '…' : err;
  return `${badge} <small title="${esc(err)}">${esc(short)}</small>`;
}

async function refreshDbStatus() {
  const cell = alias => document.getElementById(`dbstat-${alias}`);
  databases.forEach(d => { const c = cell(d.alias); if (c) c.textContent = 'Checking…'; });

  let rows;
  try {
    rows = await api('GET', '/databases/status');
  } catch (err) {
    // Non-superusers get 403 here. The dashboard stays usable; the column
    // simply reports nothing rather than showing an error.
    databases.forEach(d => { const c = cell(d.alias); if (c) c.textContent = '—'; });
    return;
  }

  rows.forEach(r => {
    const c = cell(r.alias);
    if (c) c.innerHTML = dbStatusHtml(r);
  });
}
```

- [ ] **Step 4: Verify against the running app**

Start the app: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

Open `http://localhost:8000/admin`, log in as a superuser, and confirm on the Dashboard:
1. The Databases card shows a **Status** column and a **Refresh** button.
2. Each row briefly shows `Checking…`, then resolves to a badge.
3. A reachable database shows a green `Online` badge with a latency figure.
4. Clicking **Refresh** re-runs the check and the cells return to `Checking…` then resolve.
5. The rest of the dashboard (stat cards, Recent Users) renders immediately without waiting on the status call.

To verify the offline path, add an alias pointing at a dead host (e.g. `dsn: localhost:1/dead`) in `app/database_definitions/databases.yaml`, restart the process (data-source definitions load once at startup — there is no hot reload), and confirm the row shows a red `Offline` badge with the driver error in the tooltip.

- [ ] **Step 5: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat(admin): show live connection status per database on the dashboard"
```

---

## Verification

After all five tasks:

- [ ] `pytest -v` — full suite passes (11 new tests: 3 dialect, 5 service, 3 endpoint).
- [ ] `ruff check app/ tests/` — clean.
- [ ] Dashboard shows real per-database status, refreshes on demand, and stays responsive when a database is down.
