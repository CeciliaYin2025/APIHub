# API Usage Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a superuser-only Admin UI report that shows API usage statistics per user / date range / DW / API over the existing audit table, plus a drill-down detail view exposing each request's actual parameters.

**Architecture:** Extend `api_hub_audit_logs` with `action_type`, `params`, `result_rows`; routers stash those values on `request.state` via a new `record_audit()` helper and the existing `AuditMiddleware` persists them. A new `audit_report_service` builds SQLAlchemy `GROUP BY` aggregations at read time (no pre-aggregation), exposed through `/api/v1/audit/{usage,logs,logs/{id},filters}`. The Admin UI adds one sidebar section with two full-page views in a new `usage_report.js`.

**Tech Stack:** FastAPI, SQLAlchemy 2.x (`select()` style), PostgreSQL (JSONB, `COUNT(*) FILTER`), Pydantic v2 schemas, pytest, vanilla-JS Admin SPA, Playwright for e2e.

**Spec:** `docs/superpowers/specs/2026-07-30-api-usage-report-design.md`

## Global Constraints

- Platform store is always PostgreSQL. `JSONB`, `COUNT(*) FILTER (WHERE ...)` and `date()` are fair game; do not add SQLite compatibility shims.
- No Alembic in this project. Schema changes go in `scripts/init_db.py` as idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` statements, **and** in the model so fresh installs get them via `Base.metadata.create_all`.
- Audit writes are best-effort: any failure is logged and swallowed and must never affect the response. Nothing in this plan may break that guarantee.
- `pytest -v` must pass with **no live PostgreSQL** — `tests/e2e` is excluded via `norecursedirs`. Python tests use fake session objects or compile SQL statements without executing them (see `tests/test_api_key_service.py` for the established fake-DB style). Numeric aggregation correctness is verified in the Playwright e2e suite, which spins a throwaway Postgres container.
- New settings go in `app/core/config.py` as `Settings` fields with defaults — never `os.environ` directly.
- All values interpolated into Admin UI HTML go through `esc()`.
- `params` is stored **unmasked and untruncated** by explicit decision. Do not add masking or truncation on the write path.
- Report endpoints are superuser-only via `Depends(get_current_active_superuser)`.
- Exact new settings and defaults: `audit_retention_days: int = 365`, `audit_params_inline_limit: int = 4096`.
- Exact `action_type` values: `query`, `procedure`, `write`, `definition`, `report`.
- Exact index names: `ix_audit_principal_ts`, `ix_audit_db_alias_ts`, `ix_audit_action_ts`, `ix_audit_action_type_ts`.
- Commit message style: Conventional Commits (`feat(scope):`, `test:`, `docs:`), matching recent history.

---

### Task 1: Audit schema, settings, and retention purge

**Files:**
- Modify: `app/models/audit_log.py`
- Modify: `app/core/config.py:87` (after `db_health_check_timeout`)
- Modify: `scripts/init_db.py:32-36` (after the `order_by` ALTER block)
- Create: `scripts/purge_audit_logs.py`
- Test: `tests/test_purge_audit_logs.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `AuditLog.action_type: str | None`, `AuditLog.params: dict | list | None`, `AuditLog.result_rows: int | None`
  - `settings.audit_retention_days: int`, `settings.audit_params_inline_limit: int`
  - `scripts.purge_audit_logs.purge_audit_logs(db, days: int, batch_size: int = 10000, dry_run: bool = False) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_purge_audit_logs.py`:

```python
"""Retention purge deletes only rows past the cutoff, in batches."""

from scripts.purge_audit_logs import purge_audit_logs


class _FakeQuery:
    """Mimics the db.query(...).filter(...).limit(...).all()/.count()/.delete()
    chain used by purge_audit_logs. The ids handed out by the most recent
    all() are what a following delete() removes, which is exactly how the
    real two-statement batch loop behaves."""

    def __init__(self, db, limit=None):
        self._db = db
        self._limit = limit

    def filter(self, *a, **k):
        return self

    def limit(self, n):
        return _FakeQuery(self._db, n)

    def count(self):
        return len(self._db.old_ids)

    def all(self):
        self._db.last_batch = self._db.old_ids[: self._limit]
        return [(i,) for i in self._db.last_batch]

    def delete(self, synchronize_session=False):
        batch = self._db.last_batch
        self._db.old_ids = [i for i in self._db.old_ids if i not in batch]
        self._db.deleted.extend(batch)
        self._db.last_batch = []
        return len(batch)


class _FakeDB:
    def __init__(self, old_ids):
        self.old_ids = list(old_ids)
        self.deleted = []
        self.last_batch = []
        self.commits = 0

    def query(self, *entities):
        return _FakeQuery(self)

    def commit(self):
        self.commits += 1


def test_dry_run_counts_without_deleting():
    db = _FakeDB(range(7))
    assert purge_audit_logs(db, days=30, dry_run=True) == 7
    assert db.deleted == []
    assert db.commits == 0


def test_deletes_every_stale_row_in_batches():
    db = _FakeDB(range(25))
    assert purge_audit_logs(db, days=30, batch_size=10) == 25
    assert db.old_ids == []
    assert sorted(db.deleted) == list(range(25))
    # 10 + 10 + 5 -> three committed batches, so a big backlog never sits in
    # one long transaction.
    assert db.commits == 3


def test_nothing_to_purge_is_a_no_op():
    db = _FakeDB([])
    assert purge_audit_logs(db, days=30) == 0
    assert db.commits == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_purge_audit_logs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.purge_audit_logs'`

- [ ] **Step 3: Add the model columns and indexes**

In `app/models/audit_log.py`, replace the whole file with:

```python
"""ORM model for per-request audit trail (platform PostgreSQL store)."""

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import Base


class AuditLog(Base):
    __tablename__ = "api_hub_audit_logs"

    # Composite indexes for the usage report's filter dimensions. create_all
    # builds these on fresh installs; scripts/init_db.py adds them to
    # pre-existing tables (create_all skips tables that already exist).
    __table_args__ = (
        Index("ix_audit_principal_ts", "principal", "ts"),
        Index("ix_audit_db_alias_ts", "db_alias", "ts"),
        Index("ix_audit_action_ts", "action", "ts"),
        Index("ix_audit_action_type_ts", "action_type", "ts"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(DateTime, server_default=func.now(), index=True)
    principal_type = Column(String(20))  # "user" | "api_key" | "anonymous"
    principal = Column(String(100), index=True)
    api_key_id = Column(Integer, nullable=True)
    api_key_name = Column(String(100), nullable=True)
    client_ip = Column(String(64))
    method = Column(String(10))
    path = Column(String(500))
    db_alias = Column(String(64), nullable=True)
    action = Column(String(200), nullable=True)
    status_code = Column(Integer)
    duration_ms = Column(Integer)
    # "query" | "procedure" | "write" | "definition" | "report" — lets the
    # usage report separate data-access volume from admin operations.
    action_type = Column(String(20), nullable=True)
    # Request parameters, stored unmasked and untruncated by design. Table
    # size is bounded by AUDIT_RETENTION_DAYS + scripts/purge_audit_logs.
    params = Column(JSONB, nullable=True)
    result_rows = Column(Integer, nullable=True)
```

- [ ] **Step 4: Add the settings**

In `app/core/config.py`, insert after the `db_health_check_timeout` line (currently line 87):

```python
    # Usage report / audit retention
    audit_retention_days: int = 365
    # Rows whose serialized params exceed this many bytes are returned with
    # params omitted in the detail list; the UI fetches them per row on
    # expand. Guards the API response size, not the stored size.
    audit_params_inline_limit: int = 4096
```

- [ ] **Step 5: Extend init_db with the idempotent migration**

In `scripts/init_db.py`, insert after the existing `order_by` block (currently ends line 36):

```python
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE api_hub_audit_logs ADD COLUMN IF NOT EXISTS action_type varchar(20)"))
        conn.execute(text("ALTER TABLE api_hub_audit_logs ADD COLUMN IF NOT EXISTS params jsonb"))
        conn.execute(text("ALTER TABLE api_hub_audit_logs ADD COLUMN IF NOT EXISTS result_rows integer"))
        for index_name, columns in (
            ("ix_audit_principal_ts", "principal, ts"),
            ("ix_audit_db_alias_ts", "db_alias, ts"),
            ("ix_audit_action_ts", "action, ts"),
            ("ix_audit_action_type_ts", "action_type, ts"),
        ):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON api_hub_audit_logs ({columns})"))
    print("Ensured api_hub_audit_logs report columns and indexes.")
```

- [ ] **Step 6: Write the purge script**

Create `scripts/purge_audit_logs.py`:

```python
"""Delete audit rows older than the retention window.

Usage:
    python -m scripts.purge_audit_logs [--days N] [--batch-size N] [--dry-run]

Retention is the only thing bounding api_hub_audit_logs growth: params are
stored untruncated, so a batch write payload can be megabytes per row.
Run this from cron / a scheduled task; it is deliberately not a Celery beat
job so it never competes with the write/procedure queues.
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog


def purge_audit_logs(db, days: int, batch_size: int = 10000, dry_run: bool = False) -> int:
    """Delete api_hub_audit_logs rows older than *days*.

    Deletes in committed batches so a large backlog never holds one long
    transaction (and its locks) open against a table the API writes to on
    every request. Returns the number of rows deleted, or under *dry_run*
    the number that would be deleted.
    """
    cutoff = datetime.now() - timedelta(days=days)
    if dry_run:
        return db.query(AuditLog).filter(AuditLog.ts < cutoff).count()

    deleted = 0
    while True:
        ids = [row[0] for row in db.query(AuditLog.id).filter(AuditLog.ts < cutoff).limit(batch_size).all()]
        if not ids:
            return deleted
        db.query(AuditLog).filter(AuditLog.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        deleted += len(ids)


def main():
    parser = argparse.ArgumentParser(description="Purge old api_hub_audit_logs rows")
    parser.add_argument("--days", type=int, default=settings.audit_retention_days,
                        help="Retention window in days (default: AUDIT_RETENTION_DAYS)")
    parser.add_argument("--batch-size", type=int, default=10000, help="Rows deleted per transaction")
    parser.add_argument("--dry-run", action="store_true", help="Report the row count without deleting")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        count = purge_audit_logs(db, days=args.days, batch_size=args.batch_size, dry_run=args.dry_run)
    finally:
        db.close()

    verb = "would delete" if args.dry_run else "deleted"
    print(f"Retention {args.days} days: {verb} {count} audit row(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_purge_audit_logs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Run the full suite for regressions**

Run: `pytest -q`
Expected: PASS — the model change adds columns only; no existing test asserts the column list.

- [ ] **Step 9: Commit**

```bash
git add app/models/audit_log.py app/core/config.py scripts/init_db.py scripts/purge_audit_logs.py tests/test_purge_audit_logs.py
git commit -m "feat(audit): add action_type/params/result_rows columns and retention purge"
```

---

### Task 2: `record_audit()` helper and middleware passthrough

**Files:**
- Modify: `app/middleware/audit.py:35-56` (`build_audit_row`)
- Test: `tests/test_audit_middleware.py`

**Interfaces:**
- Consumes: `AuditLog.action_type` / `params` / `result_rows` (Task 1).
- Produces: `app.middleware.audit.record_audit(request, action_type=None, action=None, params=None, result_rows=None) -> None`. Every router in Tasks 3–4 and 6 calls this. `build_audit_row()` gains the keys `action_type`, `params`, `result_rows`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit_middleware.py`:

```python
def test_build_audit_row_includes_report_fields():
    req = _Req()
    req.state = type("S", (), {
        "principal": None,
        "audit_action_type": "query",
        "audit_action": "get_orders",
        "audit_params": {"params": {"dept": "IT"}, "fetch_mode": "page"},
        "audit_result_rows": 12,
    })()
    row = build_audit_row(req, _Resp(), 5)
    assert row["action_type"] == "query"
    assert row["params"] == {"params": {"dept": "IT"}, "fetch_mode": "page"}
    assert row["result_rows"] == 12


def test_build_audit_row_report_fields_default_to_none():
    row = build_audit_row(_Req(), _Resp(), 1)
    assert row["action_type"] is None
    assert row["params"] is None
    assert row["result_rows"] is None


def test_unserializable_params_degrade_instead_of_raising():
    # A payload json cannot encode must not take the request down with it.
    class _Weird:
        def __repr__(self):
            return "<weird>"

    req = _Req()
    req.state = type("S", (), {"principal": None, "audit_params": {"blob": _Weird()}})()
    row = build_audit_row(req, _Resp(), 1)
    assert row["params"] == {"_unserializable": "{'blob': <weird>}"}


def test_record_audit_only_applies_non_none_arguments():
    # Routers call record_audit twice: once up front, once after the response
    # is built to add result_rows. The second call must not wipe the first.
    req = _Req()
    req.state = type("S", (), {"principal": None})()
    au.record_audit(req, "write", "ORDERS", params={"operation": "insert"})
    au.record_audit(req, result_rows=3)
    assert req.state.audit_action_type == "write"
    assert req.state.audit_action == "ORDERS"
    assert req.state.audit_params == {"operation": "insert"}
    assert req.state.audit_result_rows == 3


def test_record_audit_records_zero_result_rows():
    req = _Req()
    req.state = type("S", (), {"principal": None})()
    au.record_audit(req, "query", "get_orders")
    au.record_audit(req, result_rows=0)
    assert req.state.audit_result_rows == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audit_middleware.py -v`
Expected: FAIL — `KeyError: 'action_type'` and `AttributeError: module 'app.middleware.audit' has no attribute 'record_audit'`

- [ ] **Step 3: Implement the helper and extend the row builder**

In `app/middleware/audit.py`, add `import json` to the imports, then insert before `build_audit_row`:

```python
def record_audit(request: Request, action_type: str | None = None, action: str | None = None,
                 params=None, result_rows: int | None = None) -> None:
    """Stash usage-report detail on request.state for AuditMiddleware.

    The middleware runs after call_next, by which point the request body is
    already consumed — routers are the only place the parsed payload is
    available, so they hand it over here (the same pattern the pre-existing
    audit_action / audit_db_alias assignments use).

    Safe to call more than once per request: only non-None arguments are
    applied, so a router can record the action and params up front and add
    result_rows once the response has been built.
    """
    if action_type is not None:
        request.state.audit_action_type = action_type
    if action is not None:
        request.state.audit_action = action
    if params is not None:
        request.state.audit_params = params
    if result_rows is not None:
        request.state.audit_result_rows = result_rows


def _safe_params(value):
    """Render recorded params as something JSONB can store.

    Audit writes are best-effort and must never surface, so a payload json
    cannot encode degrades to its repr rather than raising out of
    build_audit_row.
    """
    if value is None:
        return None
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return {"_unserializable": repr(value)}
    return value
```

Then in `build_audit_row`, add three keys to the returned dict (after `"action"`):

```python
        "action_type": getattr(request.state, "audit_action_type", None),
        "params": _safe_params(getattr(request.state, "audit_params", None)),
        "result_rows": getattr(request.state, "audit_result_rows", None),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_audit_middleware.py -v`
Expected: PASS — including the pre-existing `test_write_audit_swallows_db_failure`, whose hand-built row dict lacks the new keys but only feeds a fake session.

- [ ] **Step 5: Commit**

```bash
git add app/middleware/audit.py tests/test_audit_middleware.py
git commit -m "feat(audit): add record_audit helper and persist action_type/params/result_rows"
```

---

### Task 3: Instrument query execution and definition CRUD

**Files:**
- Modify: `app/api/v1/queries.py` (imports, `execute`, `create_definition`, `update_definition`, `delete_definition`)
- Test: `tests/test_audit_instrumentation.py` (create)

**Interfaces:**
- Consumes: `record_audit()` (Task 2).
- Produces: audit rows with `action_type="query"` for execution and `action_type="definition"` for CRUD. Task 9's e2e spec depends on `action_type="query"` rows carrying `params.params`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_audit_instrumentation.py`. It drives the real route functions with fakes so no DB or Oracle pool is needed:

```python
"""Routers hand the usage report its action_type / params / result_rows.

These call the route functions directly with fakes: the point is the
record_audit contract, not HTTP plumbing (covered by tests/e2e).
"""

import pytest
from fastapi import HTTPException

import app.api.v1.queries as queries_api
from app.schemas.query import QueryRequest


class _State:
    principal = None


class _Req:
    def __init__(self):
        self.state = _State()


class _Def:
    permission = "query:get_orders"


def test_query_execute_records_type_params_and_rows(monkeypatch):
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: _Def())
    monkeypatch.setattr(queries_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(queries_api, "execute_query", lambda *a, **k: {
        "total": 99, "columns": ["id"], "data": [{"id": 1}, {"id": 2}],
    })

    req = _Req()
    payload = QueryRequest(query_name="get_orders", params={"dept": "IT"}, fetch_mode="page")
    queries_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "query"
    assert req.state.audit_action == "get_orders"
    assert req.state.audit_params == {"params": {"dept": "IT"}, "fetch_mode": "page"}
    # page mode reports the rows actually returned, not the grand total
    assert req.state.audit_result_rows == 2


def test_query_execute_count_mode_records_total(monkeypatch):
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: _Def())
    monkeypatch.setattr(queries_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(queries_api, "execute_query", lambda *a, **k: {
        "total": 4321, "columns": [], "data": [],
    })

    req = _Req()
    payload = QueryRequest(query_name="get_orders", fetch_mode="count")
    queries_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_result_rows == 4321


def test_query_execute_records_params_even_when_definition_missing(monkeypatch):
    # A 404 still has to leave an audited row: "why does this query keep
    # failing" is the main thing the detail view is for.
    monkeypatch.setattr(queries_api.sql_registry, "get", lambda name, db_alias=None: None)

    req = _Req()
    payload = QueryRequest(query_name="ghost", params={"a": 1})
    with pytest.raises(HTTPException) as excinfo:
        queries_api.execute(req, payload, db="dw1", current_user=object())

    assert excinfo.value.status_code == 404
    assert req.state.audit_action_type == "query"
    assert req.state.audit_params == {"params": {"a": 1}, "fetch_mode": "page"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audit_instrumentation.py -v`
Expected: FAIL — `AttributeError: '_State' object has no attribute 'audit_action_type'`

- [ ] **Step 3: Instrument `queries.py`**

Add the import near the other app imports in `app/api/v1/queries.py`:

```python
from app.middleware.audit import record_audit
```

Replace line 393 (`request.state.audit_action = payload.query_name`) with:

```python
    record_audit(request, "query", payload.query_name,
                 params={"params": payload.params, "fetch_mode": payload.fetch_mode})
```

Then insert immediately before the `return QueryResponse(` at line 421:

```python
    # count mode returns no rows, so the matching total is the meaningful
    # volume figure for the report.
    record_audit(request, result_rows=result["total"] if payload.fetch_mode == "count" else len(result["data"]))
```

Add `request: Request` as the first parameter of `create_definition`, `update_definition` and `delete_definition`, and record after the superuser check:

In `create_definition`, after `_require_superuser(current_user)`:

```python
    record_audit(request, "definition", body.name, params=body.model_dump(mode="json"))
```

In `update_definition`, after `_require_superuser(current_user)`:

```python
    record_audit(request, "definition", name, params=body.model_dump(mode="json", exclude_none=True))
```

In `delete_definition`, after `_require_superuser(current_user)`:

```python
    record_audit(request, "definition", name)
```

`Request` is already imported in this module.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_audit_instrumentation.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite for regressions**

Run: `pytest -q`
Expected: PASS. Note the three definition endpoints gained a required positional `request` parameter — if any existing test calls them directly it must pass a request object; grep with `grep -rn "create_definition\|update_definition\|delete_definition" tests/` and fix any caller.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/queries.py tests/test_audit_instrumentation.py
git commit -m "feat(audit): record action_type/params/result_rows for query execution and definition CRUD"
```

---

### Task 4: Instrument procedures and writes

**Files:**
- Modify: `app/api/v1/procedures.py` (whole file — body parameter rename)
- Modify: `app/api/v1/writes.py:44-137` (body parameter rename + instrumentation)
- Test: `tests/test_audit_instrumentation.py` (append)

**Interfaces:**
- Consumes: `record_audit()` (Task 2).
- Produces: audit rows with `action_type="procedure"` (action = procedure name) and `action_type="write"` (action = table name).

**Note:** both modules currently name the request **body** parameter `request`, which collides with FastAPI's `Request`. Rename the body parameter to `payload` (matching `queries.py`) and inject the real `request: Request`. The body parameter name is not part of the HTTP contract, so no API change results.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit_instrumentation.py`:

```python
import app.api.v1.procedures as procedures_api
import app.api.v1.writes as writes_api
from app.schemas.query import BatchOperation, BatchWriteRequest, ProcedureRequest, WriteRequest


def test_procedure_execute_records_type_and_params(monkeypatch):
    monkeypatch.setattr(procedures_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    import app.services.procedure_service as ps
    monkeypatch.setattr(ps, "execute_procedure", lambda **k: {"result": None, "out_params": {}})

    req = _Req()
    payload = ProcedureRequest(name="PKG.REBUILD", params={"day": "2026-07-30"}, out_params=["rc"])
    procedures_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "procedure"
    assert req.state.audit_action == "PKG.REBUILD"
    assert req.state.audit_params == {
        "params": {"day": "2026-07-30"}, "out_params": ["rc"], "is_function": False,
    }


def test_write_execute_records_table_and_affected_rows(monkeypatch):
    monkeypatch.setattr(writes_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(writes_api, "execute_write", lambda **k: 3)

    req = _Req()
    payload = WriteRequest(operation="insert", table="ORDERS", data=[{"id": 1}])
    writes_api.execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "write"
    assert req.state.audit_action == "ORDERS"
    assert req.state.audit_params == {
        "operation": "insert", "data": [{"id": 1}], "where": None, "async_mode": False,
    }
    assert req.state.audit_result_rows == 3


def test_batch_write_records_operations_and_total_affected(monkeypatch):
    monkeypatch.setattr(writes_api, "PermissionChecker", lambda *a, **k: (lambda user: None))
    monkeypatch.setattr(writes_api, "execute_batch_write", lambda **k: {
        "total_affected": 5, "operation_count": 2, "committed": True, "results": [],
    })

    req = _Req()
    payload = BatchWriteRequest(table="ORDERS", operations=[
        BatchOperation(operation="insert", data=[{"id": 1}]),
        BatchOperation(operation="delete", where={"id": 2}),
    ])
    writes_api.batch_execute(req, payload, db="dw1", current_user=object())

    assert req.state.audit_action_type == "write"
    assert req.state.audit_action == "ORDERS"
    assert req.state.audit_params["async_mode"] is False
    assert len(req.state.audit_params["operations"]) == 2
    assert req.state.audit_result_rows == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audit_instrumentation.py -v`
Expected: FAIL — `TypeError: execute() got multiple values for argument 'db'` (the route still takes the body as its first parameter).

- [ ] **Step 3: Rewrite `procedures.py`**

Replace `app/api/v1/procedures.py` with:

```python
"""Endpoints for executing Oracle stored procedures and functions.

All endpoints require a ``{db}`` path parameter to select the target database.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.databases import get_db_alias
from app.middleware.audit import record_audit
from app.models.user import User
from app.schemas.query import ProcedureRequest, ProcedureResponse, TaskStatusResponse
from app.services.auth_service import PermissionChecker, get_current_user
from app.tasks.procedure_tasks import async_procedure

router = APIRouter(tags=["Procedures"])


def _audit_params(payload: ProcedureRequest) -> dict:
    return {"params": payload.params, "out_params": payload.out_params, "is_function": payload.is_function}


@router.post("/{db}/procedures/execute", response_model=ProcedureResponse)
def execute(
    request: Request,
    payload: ProcedureRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute an Oracle procedure or function synchronously."""
    record_audit(request, "procedure", payload.name, params=_audit_params(payload))

    resource = f"procedure:{payload.name}"
    checker = PermissionChecker(resource, "execute")
    checker(current_user)

    from app.services.procedure_service import execute_procedure

    try:
        result = execute_procedure(
            name=payload.name,
            params=payload.params,
            out_params=payload.out_params,
            is_function=payload.is_function,
            db_alias=db,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Procedure execution error: {e}")

    return ProcedureResponse(name=payload.name, **result)


@router.post("/{db}/procedures/execute-async", response_model=TaskStatusResponse)
def execute_async(
    request: Request,
    payload: ProcedureRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
    """Execute an Oracle procedure or function asynchronously via Celery."""
    record_audit(request, "procedure", payload.name, params=_audit_params(payload))

    resource = f"procedure:{payload.name}"
    checker = PermissionChecker(resource, "execute")
    checker(current_user)

    task = async_procedure.delay(
        name=payload.name,
        params=payload.params,
        out_params=payload.out_params,
        is_function=payload.is_function,
        db_alias=db,
    )
    return TaskStatusResponse(task_id=task.id, status="PENDING")
```

- [ ] **Step 4: Instrument `writes.py`**

In `app/api/v1/writes.py`, change the import line to `from fastapi import APIRouter, Depends, HTTPException, Request` and add `from app.middleware.audit import record_audit`.

In `execute` (line 44), change the signature to:

```python
@router.post("/{db}/writes/execute", response_model=WriteResponse)
def execute(
    request: Request,
    payload: WriteRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
```

Replace every `request.` reference in the body with `payload.`, and insert as the first statement of the body:

```python
    record_audit(request, "write", payload.table, params={
        "operation": payload.operation, "data": payload.data,
        "where": payload.where, "async_mode": payload.async_mode,
    })
```

Then insert immediately before `return WriteResponse(` (the sync path, after the `execute_write` call):

```python
    record_audit(request, result_rows=affected)
```

In `batch_execute` (line 89), change the signature to:

```python
@router.post("/{db}/writes/batch", response_model=BatchWriteResponse)
def batch_execute(
    request: Request,
    payload: BatchWriteRequest,
    db: str = Depends(get_db_alias),
    current_user: User = Depends(get_current_user),
):
```

Replace every `request.` reference with `payload.`, and insert after the `ops = [op.model_dump() for op in payload.operations]` line:

```python
    record_audit(request, "write", payload.table, params={"operations": ops, "async_mode": payload.async_mode})
```

Then insert immediately before `return BatchWriteResponse(` (the sync path):

```python
    record_audit(request, result_rows=result["total_affected"])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_audit_instrumentation.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the full suite for regressions**

Run: `pytest -q`
Expected: PASS. The `request` → `payload` rename touches only these two modules; confirm with `grep -rn "procedures_api\|writes_api\|procedures.execute\|writes.execute" tests/`.

- [ ] **Step 7: Commit**

```bash
git add app/api/v1/procedures.py app/api/v1/writes.py tests/test_audit_instrumentation.py
git commit -m "feat(audit): record action_type/params/result_rows for procedure and write endpoints"
```

---

### Task 5: Usage report aggregation service

**Files:**
- Create: `app/services/audit_report_service.py`
- Test: `tests/test_audit_report_service.py`

**Interfaces:**
- Consumes: `AuditLog` columns from Task 1.
- Produces (Task 6 calls exactly these):
  - `resolve_date_range(date_from: date | None, date_to: date | None) -> tuple[datetime, datetime]`
  - `parse_group_by(raw: str | None) -> list[str]`
  - `parse_action_types(raw: str | None) -> list[str]`
  - `parse_sort(raw: str | None) -> tuple[str, bool]`
  - `build_filters(ts_from, ts_to, *, principal=None, db_alias=None, action=None, action_types=(), status_code=None, min_duration_ms=None) -> list`
  - `build_usage_statement(*, group_by, clauses, sort_column, sort_desc, page, page_size) -> Select`
  - `usage_report(db, *, group_by, clauses, sort_column, sort_desc, page, page_size) -> dict`
  - `logs_page(db, *, clauses, page, page_size, inline_limit) -> dict`
  - `get_log(db, log_id: int) -> dict | None`
  - `filter_options(db, *, ts_from, ts_to) -> dict`
  - Module constants `GROUP_COLUMNS`, `GROUP_KEYS`, `SORT_COLUMNS`, `DEFAULT_ACTION_TYPES`, `VALID_ACTION_TYPES`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit_report_service.py`:

```python
"""Usage report query building and pure helpers.

No live PostgreSQL is available to `pytest` (tests/e2e is excluded via
norecursedirs), so these tests exercise the parsing/validation helpers
directly and assert on the compiled SQL of the built statements. The
numeric correctness of the aggregation is covered by
tests/e2e/usage-report.spec.js against a real Postgres.
"""

from datetime import date, datetime

import pytest
from sqlalchemy.dialects import postgresql

import app.services.audit_report_service as svc


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect())).replace("\n", " ")


# ── resolve_date_range ───────────────────────────────────────────────────

def test_date_range_covers_whole_end_day():
    ts_from, ts_to = svc.resolve_date_range(date(2026, 7, 1), date(2026, 7, 29))
    assert ts_from == datetime(2026, 7, 1, 0, 0, 0)
    assert ts_to.date() == date(2026, 7, 29)
    assert (ts_to.hour, ts_to.minute, ts_to.second) == (23, 59, 59)


def test_date_range_defaults_to_last_30_days_inclusive():
    ts_from, ts_to = svc.resolve_date_range(None, date(2026, 7, 30))
    assert ts_from.date() == date(2026, 7, 1)  # 30 days including the 30th


def test_date_range_rejects_inverted_range():
    with pytest.raises(ValueError, match="date_from"):
        svc.resolve_date_range(date(2026, 7, 30), date(2026, 7, 1))


# ── parse_group_by ───────────────────────────────────────────────────────

def test_group_by_defaults_to_user_db_api():
    assert svc.parse_group_by(None) == ["user", "db", "api"]


def test_group_by_empty_string_means_totals_only():
    assert svc.parse_group_by("") == []


def test_group_by_dedupes_and_preserves_order():
    assert svc.parse_group_by("day, user ,day") == ["day", "user"]


def test_group_by_rejects_unknown_token():
    with pytest.raises(ValueError, match="ip"):
        svc.parse_group_by("user,ip")


# ── parse_action_types ───────────────────────────────────────────────────

def test_action_types_default_excludes_definition_and_report():
    assert svc.parse_action_types(None) == ["query", "procedure", "write"]


def test_action_types_empty_string_means_no_filter():
    assert svc.parse_action_types("") == []


def test_action_types_rejects_unknown_value():
    with pytest.raises(ValueError, match="login"):
        svc.parse_action_types("query,login")


# ── parse_sort ───────────────────────────────────────────────────────────

def test_sort_defaults_to_calls_descending():
    assert svc.parse_sort(None) == ("calls", True)


def test_sort_ascending_without_minus():
    assert svc.parse_sort("avg_duration_ms") == ("avg_duration_ms", False)


def test_sort_rejects_unknown_column():
    with pytest.raises(ValueError, match="principal"):
        svc.parse_sort("-principal")


# ── build_filters ────────────────────────────────────────────────────────

def test_filters_always_bound_the_time_window():
    clauses = svc.build_filters(datetime(2026, 7, 1), datetime(2026, 7, 29, 23, 59, 59))
    assert len(clauses) == 2


def test_filters_add_one_clause_per_supplied_dimension():
    clauses = svc.build_filters(
        datetime(2026, 7, 1), datetime(2026, 7, 29),
        principal="alice", db_alias="DW1", action="get_orders",
        action_types=["query"], status_code=500, min_duration_ms=100,
    )
    assert len(clauses) == 8


def test_empty_action_types_adds_no_clause():
    clauses = svc.build_filters(datetime(2026, 7, 1), datetime(2026, 7, 2), action_types=[])
    assert len(clauses) == 2


# ── build_usage_statement ────────────────────────────────────────────────

def _stmt(group_by=("user", "db", "api"), sort_column="calls", sort_desc=True, page=1, page_size=50):
    clauses = svc.build_filters(datetime(2026, 7, 1), datetime(2026, 7, 29))
    return svc.build_usage_statement(group_by=list(group_by), clauses=clauses,
                                    sort_column=sort_column, sort_desc=sort_desc,
                                    page=page, page_size=page_size)


def test_statement_groups_by_the_selected_dimensions():
    sql = _sql(_stmt(group_by=("user", "day")))
    assert "GROUP BY api_hub_audit_logs.principal, date(api_hub_audit_logs.ts)" in sql
    # unselected dimensions must not leak into the grouping
    assert "db_alias" not in sql.split("GROUP BY")[1]


def test_statement_splits_success_and_failure_with_filtered_counts():
    sql = _sql(_stmt())
    assert "FILTER (WHERE api_hub_audit_logs.status_code < " in sql
    assert "FILTER (WHERE api_hub_audit_logs.status_code >= " in sql


def test_statement_sorts_and_paginates():
    sql = _sql(_stmt(sort_column="avg_duration_ms", sort_desc=False, page=3, page_size=25))
    assert "ORDER BY avg_duration_ms" in sql
    assert "DESC" not in sql.split("ORDER BY")[1]
    assert "LIMIT" in sql and "OFFSET" in sql


def test_statement_sorts_descending_when_requested():
    sql = _sql(_stmt(sort_column="last_used_at", sort_desc=True))
    assert "ORDER BY last_used_at DESC" in sql


# ── _log_to_dict ─────────────────────────────────────────────────────────

class _Row:
    """Stands in for an AuditLog ORM row."""

    def __init__(self, params):
        self.id = 7
        self.ts = datetime(2026, 7, 30, 10, 12, 33)
        self.principal = "alice"
        self.principal_type = "user"
        self.api_key_name = None
        self.client_ip = "10.1.2.3"
        self.method = "POST"
        self.path = "/api/v1/dw1/queries/execute"
        self.db_alias = "DW1"
        self.action_type = "query"
        self.action = "get_orders"
        self.status_code = 200
        self.duration_ms = 145
        self.result_rows = 12
        self.params = params


def test_small_params_are_inlined():
    item = svc._log_to_dict(_Row({"dept": "IT"}), inline_limit=4096)
    assert item["params"] == {"dept": "IT"}
    assert item["params_omitted"] is False
    assert item["params_bytes"] > 0


def test_oversized_params_are_omitted_with_their_size():
    big = {"rows": [{"v": "x" * 100} for _ in range(200)]}
    item = svc._log_to_dict(_Row(big), inline_limit=4096)
    assert item["params"] is None
    assert item["params_omitted"] is True
    assert item["params_bytes"] > 4096


def test_no_inline_limit_always_returns_full_params():
    big = {"rows": [{"v": "x" * 100} for _ in range(200)]}
    item = svc._log_to_dict(_Row(big), inline_limit=None)
    assert item["params"] == big
    assert item["params_omitted"] is False


def test_null_params_stay_null():
    item = svc._log_to_dict(_Row(None), inline_limit=4096)
    assert item["params"] is None
    assert item["params_omitted"] is False
    assert item["params_bytes"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audit_report_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.audit_report_service'`

- [ ] **Step 3: Write the service**

Create `app/services/audit_report_service.py`:

```python
"""Read-time aggregation over api_hub_audit_logs for the API usage report.

Statistics are computed with a live GROUP BY rather than a pre-aggregated
table: the report lets the caller choose which dimensions to group by, and
retention keeps the table's size bounded. The composite indexes on
(principal|db_alias|action|action_type, ts) are what make this cheap.
"""

import json
from datetime import date, datetime, time, timedelta

from sqlalchemy import Integer, and_, asc, desc, func, select

from app.models.audit_log import AuditLog

# group_by token -> the expression to group on.
GROUP_COLUMNS = {
    "user": AuditLog.principal,
    "db": AuditLog.db_alias,
    "api": AuditLog.action,
    "day": func.date(AuditLog.ts),
}

# group_by token -> the response key it fills in.
GROUP_KEYS = {"user": "principal", "db": "db_alias", "api": "action", "day": "day"}

DEFAULT_GROUP_BY = ("user", "db", "api")

SORT_COLUMNS = ("calls", "success", "failed", "avg_duration_ms", "max_duration_ms",
                "total_rows", "last_used_at")

# The report is about data access, so admin operations (definition) and the
# report's own reads (report) are excluded unless asked for explicitly.
DEFAULT_ACTION_TYPES = ("query", "procedure", "write")
VALID_ACTION_TYPES = ("query", "procedure", "write", "definition", "report")

_METRIC_KEYS = ("calls", "success", "failed", "avg_duration_ms", "max_duration_ms",
                "total_rows", "last_used_at")

_DEFAULT_RANGE_DAYS = 30


# ── parsing / validation (pure) ──────────────────────────────────────────


def resolve_date_range(date_from: date | None, date_to: date | None) -> tuple[datetime, datetime]:
    """Turn an inclusive date range into inclusive datetime bounds.

    Defaults to the last 30 days ending today. ts is a naive DateTime on the
    platform store, so no timezone conversion happens anywhere here.
    """
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=_DEFAULT_RANGE_DAYS - 1))
    if start > end:
        raise ValueError("date_from must not be after date_to")
    return datetime.combine(start, time.min), datetime.combine(end, time.max.replace(microsecond=0))


def _parse_csv(raw: str, valid, label: str) -> list[str]:
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    unknown = [t for t in tokens if t not in valid]
    if unknown:
        raise ValueError(f"Unknown {label} value(s): {', '.join(unknown)}. Valid: {', '.join(valid)}")
    deduped = []
    for token in tokens:
        if token not in deduped:
            deduped.append(token)
    return deduped


def parse_group_by(raw: str | None) -> list[str]:
    """None -> the default dimensions; "" -> no grouping (totals only)."""
    if raw is None:
        return list(DEFAULT_GROUP_BY)
    return _parse_csv(raw, GROUP_COLUMNS, "group_by")


def parse_action_types(raw: str | None) -> list[str]:
    """None -> the data-access default; "" -> no action_type filter at all."""
    if raw is None:
        return list(DEFAULT_ACTION_TYPES)
    return _parse_csv(raw, VALID_ACTION_TYPES, "action_type")


def parse_sort(raw: str | None) -> tuple[str, bool]:
    """Return (column, descending). Column is whitelisted, so it is safe to
    hand to ORDER BY as a label reference."""
    value = (raw or "-calls").strip()
    descending = value.startswith("-")
    column = value.lstrip("-")
    if column not in SORT_COLUMNS:
        raise ValueError(f"Unknown sort column '{column}'. Valid: {', '.join(SORT_COLUMNS)}")
    return column, descending


def build_filters(ts_from: datetime, ts_to: datetime, *, principal: str | None = None,
                  db_alias: str | None = None, action: str | None = None,
                  action_types=(), status_code: int | None = None,
                  min_duration_ms: int | None = None) -> list:
    """WHERE clauses shared by every report query."""
    clauses = [AuditLog.ts >= ts_from, AuditLog.ts <= ts_to]
    if principal:
        clauses.append(AuditLog.principal == principal)
    if db_alias:
        clauses.append(AuditLog.db_alias == db_alias)
    if action:
        clauses.append(AuditLog.action == action)
    if action_types:
        clauses.append(AuditLog.action_type.in_(list(action_types)))
    if status_code is not None:
        clauses.append(AuditLog.status_code == status_code)
    if min_duration_ms is not None:
        clauses.append(AuditLog.duration_ms >= min_duration_ms)
    return clauses


# ── statement building ───────────────────────────────────────────────────


def _metric_columns() -> list:
    return [
        func.count().label("calls"),
        func.count().filter(AuditLog.status_code < 400).label("success"),
        func.count().filter(AuditLog.status_code >= 400).label("failed"),
        func.cast(func.round(func.coalesce(func.avg(AuditLog.duration_ms), 0)), Integer).label("avg_duration_ms"),
        func.coalesce(func.max(AuditLog.duration_ms), 0).label("max_duration_ms"),
        func.coalesce(func.sum(AuditLog.result_rows), 0).label("total_rows"),
        func.max(AuditLog.ts).label("last_used_at"),
    ]


def _group_columns(group_by: list[str]) -> list:
    return [GROUP_COLUMNS[token].label(GROUP_KEYS[token]) for token in group_by]


def build_usage_statement(*, group_by: list[str], clauses: list, sort_column: str,
                          sort_desc: bool, page: int, page_size: int):
    """The paginated per-group statistics query."""
    group_cols = _group_columns(group_by)
    order = desc(sort_column) if sort_desc else asc(sort_column)
    return (
        select(*group_cols, *_metric_columns())
        .where(and_(*clauses))
        .group_by(*group_cols)
        .order_by(order)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )


# ── execution ────────────────────────────────────────────────────────────


def _row_to_metrics(row) -> dict:
    return {key: row[key] for key in _METRIC_KEYS}


def usage_report(db, *, group_by: list[str], clauses: list, sort_column: str,
                 sort_desc: bool, page: int, page_size: int) -> dict:
    """Statistics for the report table, plus the grand totals row."""
    totals_row = db.execute(select(*_metric_columns()).where(and_(*clauses))).mappings().one()
    totals = _row_to_metrics(totals_row)

    if not group_by:
        return {"total_groups": 0, "page": page, "page_size": page_size, "totals": totals, "items": []}

    group_cols = _group_columns(group_by)
    total_groups = db.execute(
        select(func.count()).select_from(
            select(*group_cols).where(and_(*clauses)).group_by(*group_cols).subquery()
        )
    ).scalar_one()

    stmt = build_usage_statement(group_by=group_by, clauses=clauses, sort_column=sort_column,
                                sort_desc=sort_desc, page=page, page_size=page_size)
    selected_keys = [GROUP_KEYS[token] for token in group_by]
    items = []
    for row in db.execute(stmt).mappings():
        item = {key: None for key in GROUP_KEYS.values()}
        item.update({key: row[key] for key in selected_keys})
        item.update(_row_to_metrics(row))
        items.append(item)

    return {"total_groups": total_groups, "page": page, "page_size": page_size,
            "totals": totals, "items": items}


def _log_to_dict(row: AuditLog, inline_limit: int | None) -> dict:
    """One detail row. Oversized params are replaced by their size so the list
    response stays small; the UI fetches them per row from get_log()."""
    params = row.params
    omitted = False
    size = None
    if params is not None:
        size = len(json.dumps(params, ensure_ascii=False, default=str).encode("utf-8"))
        if inline_limit is not None and size > inline_limit:
            params = None
            omitted = True
    return {
        "id": row.id, "ts": row.ts, "principal": row.principal,
        "principal_type": row.principal_type, "api_key_name": row.api_key_name,
        "client_ip": row.client_ip, "method": row.method, "path": row.path,
        "db_alias": row.db_alias, "action_type": row.action_type, "action": row.action,
        "status_code": row.status_code, "duration_ms": row.duration_ms,
        "result_rows": row.result_rows,
        "params": params, "params_omitted": omitted, "params_bytes": size,
    }


def logs_page(db, *, clauses: list, page: int, page_size: int, inline_limit: int | None) -> dict:
    """Newest-first page of individual requests."""
    total = db.execute(select(func.count()).select_from(AuditLog).where(and_(*clauses))).scalar_one()
    stmt = (
        select(AuditLog)
        .where(and_(*clauses))
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = [_log_to_dict(row, inline_limit) for row in db.execute(stmt).scalars()]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def get_log(db, log_id: int) -> dict | None:
    """One request with its params always inlined, however large."""
    row = db.execute(select(AuditLog).where(AuditLog.id == log_id)).scalars().first()
    return None if row is None else _log_to_dict(row, None)


def filter_options(db, *, ts_from: datetime, ts_to: datetime) -> dict:
    """Distinct values seen in the window, for the UI's filter dropdowns."""

    def distinct(column) -> list[str]:
        stmt = (
            select(column)
            .where(and_(column.isnot(None), AuditLog.ts >= ts_from, AuditLog.ts <= ts_to))
            .distinct()
            .order_by(column)
        )
        return [value for (value,) in db.execute(stmt)]

    return {
        "principals": distinct(AuditLog.principal),
        "db_aliases": distinct(AuditLog.db_alias),
        "actions": distinct(AuditLog.action),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_audit_report_service.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/audit_report_service.py tests/test_audit_report_service.py
git commit -m "feat(audit): add usage report aggregation service"
```

---

### Task 6: Report endpoints

**Files:**
- Create: `app/schemas/audit.py`
- Create: `app/api/v1/audit.py`
- Modify: `app/main.py:65-74` (router registration)
- Test: `tests/test_audit_endpoints.py` (create)

**Interfaces:**
- Consumes: everything `audit_report_service` produces (Task 5), `record_audit()` (Task 2), `settings.audit_params_inline_limit` (Task 1).
- Produces: `GET /api/v1/audit/usage`, `/audit/logs`, `/audit/logs/{log_id}`, `/audit/filters`. Tasks 7–9 consume these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit_endpoints.py`:

```python
"""Report endpoints: superuser gating, parameter validation, wiring.

The service layer is stubbed — its own behaviour is covered by
tests/test_audit_report_service.py. What matters here is that the router
validates input, maps ValueError to 400, and passes the resolved values
through.
"""

import pytest
from fastapi import HTTPException

import app.api.v1.audit as audit_api


class _State:
    principal = None


class _Req:
    def __init__(self):
        self.state = _State()


def _ok(**overrides):
    """Default kwargs for audit_api.usage()."""
    base = dict(request=_Req(), date_from=None, date_to=None, principal=None, db_alias=None,
                action=None, action_type=None, group_by=None, sort=None, page=1, page_size=50,
                _admin=object(), pgdb=object())
    base.update(overrides)
    return base


def test_usage_rejects_inverted_date_range():
    from datetime import date
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(date_from=date(2026, 7, 30), date_to=date(2026, 7, 1)))
    assert excinfo.value.status_code == 400
    assert "date_from" in excinfo.value.detail


def test_usage_rejects_unknown_group_by():
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(group_by="user,ip"))
    assert excinfo.value.status_code == 400
    assert "ip" in excinfo.value.detail


def test_usage_rejects_unknown_sort():
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(sort="-principal"))
    assert excinfo.value.status_code == 400


def test_usage_rejects_unknown_action_type():
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(action_type="query,login"))
    assert excinfo.value.status_code == 400


def test_usage_passes_resolved_values_to_the_service(monkeypatch):
    seen = {}

    def _fake(db, **kwargs):
        seen.update(kwargs)
        return {"total_groups": 0, "page": 1, "page_size": 50,
                "totals": {}, "items": []}

    monkeypatch.setattr(audit_api.svc, "usage_report", _fake)
    audit_api.usage(**_ok(group_by="day", sort="avg_duration_ms", principal="alice"))

    assert seen["group_by"] == ["day"]
    assert seen["sort_column"] == "avg_duration_ms"
    assert seen["sort_desc"] is False
    # ts window + action_type default + principal
    assert len(seen["clauses"]) == 4


def test_usage_marks_itself_as_a_report_call(monkeypatch):
    monkeypatch.setattr(audit_api.svc, "usage_report", lambda db, **k: {
        "total_groups": 0, "page": 1, "page_size": 50, "totals": {}, "items": []})
    kwargs = _ok()
    audit_api.usage(**kwargs)
    assert kwargs["request"].state.audit_action_type == "report"
    assert kwargs["request"].state.audit_action == "usage"


def test_log_detail_404s_when_missing(monkeypatch):
    monkeypatch.setattr(audit_api.svc, "get_log", lambda db, log_id: None)
    with pytest.raises(HTTPException) as excinfo:
        audit_api.log_detail(_Req(), 999, _admin=object(), pgdb=object())
    assert excinfo.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audit_endpoints.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.v1.audit'`

- [ ] **Step 3: Write the schemas**

Create `app/schemas/audit.py`:

```python
"""Pydantic schemas for the API usage report endpoints."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class UsageMetrics(BaseModel):
    calls: int = 0
    success: int = 0
    failed: int = 0
    avg_duration_ms: int = 0
    max_duration_ms: int = 0
    total_rows: int = 0
    last_used_at: datetime | None = None


class UsageItem(UsageMetrics):
    """One group. Dimensions not selected via group_by come back as null."""
    principal: str | None = None
    db_alias: str | None = None
    action: str | None = None
    day: date | None = None


class UsageReport(BaseModel):
    total_groups: int
    page: int
    page_size: int
    totals: UsageMetrics
    items: list[UsageItem]


class AuditLogItem(BaseModel):
    id: int
    ts: datetime | None = None
    principal: str | None = None
    principal_type: str | None = None
    api_key_name: str | None = None
    client_ip: str | None = None
    method: str | None = None
    path: str | None = None
    db_alias: str | None = None
    action_type: str | None = None
    action: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    result_rows: int | None = None
    params: Any | None = None
    # True when params exceeded AUDIT_PARAMS_INLINE_LIMIT and must be fetched
    # from GET /audit/logs/{id} instead.
    params_omitted: bool = False
    params_bytes: int | None = None


class AuditLogPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AuditLogItem]


class FilterOptions(BaseModel):
    principals: list[str]
    db_aliases: list[str]
    actions: list[str]
```

- [ ] **Step 4: Write the router**

Create `app/api/v1/audit.py`:

```python
"""API usage report — statistics and per-request detail over the audit trail.

Superuser-only: these endpoints expose every caller's request parameters.
Not mounted under /{db}/ because the report compares usage *across* data
sources; db_alias is a filter, not a route segment.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.middleware.audit import record_audit
from app.models.user import User
from app.schemas.audit import AuditLogItem, AuditLogPage, FilterOptions, UsageReport
from app.services import audit_report_service as svc
from app.services.auth_service import get_current_active_superuser

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/usage", response_model=UsageReport)
def usage(
    request: Request,
    date_from: date | None = Query(None, description="Inclusive start date (default: 30 days ago)"),
    date_to: date | None = Query(None, description="Inclusive end date (default: today)"),
    principal: str | None = Query(None, description="Filter by username"),
    db_alias: str | None = Query(None, description="Filter by data source alias"),
    action: str | None = Query(None, description="Filter by API name"),
    action_type: str | None = Query(None, description="Comma-separated: query,procedure,write,definition,report"),
    group_by: str | None = Query(None, description="Comma-separated: user,db,api,day"),
    sort: str | None = Query(None, description="Sort column, '-' prefix for descending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """API usage statistics, grouped by the requested dimensions."""
    record_audit(request, "report", "usage")
    try:
        ts_from, ts_to = svc.resolve_date_range(date_from, date_to)
        groups = svc.parse_group_by(group_by)
        action_types = svc.parse_action_types(action_type)
        sort_column, sort_desc = svc.parse_sort(sort)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    clauses = svc.build_filters(ts_from, ts_to, principal=principal, db_alias=db_alias,
                               action=action, action_types=action_types)
    return svc.usage_report(pgdb, group_by=groups, clauses=clauses, sort_column=sort_column,
                            sort_desc=sort_desc, page=page, page_size=page_size)


@router.get("/filters", response_model=FilterOptions)
def filters(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """Distinct filter values seen in the window, for the UI's dropdowns."""
    record_audit(request, "report", "filters")
    try:
        ts_from, ts_to = svc.resolve_date_range(date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return svc.filter_options(pgdb, ts_from=ts_from, ts_to=ts_to)


@router.get("/logs", response_model=AuditLogPage)
def logs(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    principal: str | None = Query(None),
    db_alias: str | None = Query(None),
    action: str | None = Query(None),
    action_type: str | None = Query(None),
    status_code: int | None = Query(None),
    min_duration_ms: int | None = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """Individual requests behind the statistics, newest first."""
    record_audit(request, "report", "logs")
    try:
        ts_from, ts_to = svc.resolve_date_range(date_from, date_to)
        action_types = svc.parse_action_types(action_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    clauses = svc.build_filters(ts_from, ts_to, principal=principal, db_alias=db_alias,
                               action=action, action_types=action_types,
                               status_code=status_code, min_duration_ms=min_duration_ms)
    return svc.logs_page(pgdb, clauses=clauses, page=page, page_size=page_size,
                         inline_limit=settings.audit_params_inline_limit)


@router.get("/logs/{log_id}", response_model=AuditLogItem)
def log_detail(
    request: Request,
    log_id: int,
    _admin: User = Depends(get_current_active_superuser),
    pgdb: Session = Depends(get_db),
):
    """One request with its params always inlined, however large."""
    record_audit(request, "report", "log_detail")
    item = svc.get_log(pgdb, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Audit log {log_id} not found")
    return item
```

- [ ] **Step 5: Register the router**

In `app/main.py`, add `audit` to the `app.api.v1` import list, then add after the `databases` router registration (line 67):

```python
app.include_router(audit.router, prefix=settings.api_prefix)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_audit_endpoints.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Verify the routes are actually mounted**

Run: `python -c "from app.main import app; print([r.path for r in app.routes if '/audit' in r.path])"`
Expected: the four `/api/v1/audit/...` paths. `/logs/{log_id}` must not shadow `/filters` — since `/filters` is a sibling of `/logs`, not a child, ordering is not a hazard here, but confirm all four appear.

- [ ] **Step 8: Run the full suite**

Run: `pytest -q`
Expected: PASS. `tests/test_middleware_wiring.py` and `tests/test_root_path.py` touch `app.main`; confirm neither asserts an exact router count.

- [ ] **Step 9: Commit**

```bash
git add app/schemas/audit.py app/api/v1/audit.py app/main.py tests/test_audit_endpoints.py
git commit -m "feat(audit): add superuser-only usage report and detail endpoints"
```

---

### Task 7: Admin UI — statistics page

**Files:**
- Create: `app/admin/js/usage_report.js`
- Modify: `app/admin/index.html:79-83` (sidebar), `:119` (script tag)
- Modify: `app/admin/js/app.js:176-199` (`navigateTo` titles + renderers)

**Interfaces:**
- Consumes: `GET /audit/usage`, `GET /audit/filters` (Task 6); `api()`, `esc()`, `toast()`, `currentUser` globals from `app.js`.
- Produces: `renderUsageReport()` (registered in `navigateTo`), `enterUsageView(title)`, and module state `urState`. Task 8 adds the detail view to this same file and calls `enterUsageView()` and `urState`.

- [ ] **Step 1: Add the sidebar entry and script tag**

In `app/admin/index.html`, inside the `Operations` nav section after the `tasks` link (line 82):

```html
      <a href="#" data-page="usage-report" id="navUsageReport">
        <span class="nav-icon">&#128202;</span> API Usage Report
      </a>
```

And replace the closing script line (119) with:

```html
<script src="{{BASE_PATH}}/admin/js/app.js"></script>
<script src="{{BASE_PATH}}/admin/js/usage_report.js"></script>
```

- [ ] **Step 2: Register the page in `navigateTo`**

In `app/admin/js/app.js`, add to the `titles` map:

```javascript
    'usage-report': 'API Usage Report',
```

and to the `renderers` map:

```javascript
    'usage-report': renderUsageReport,
```

Then, in `showApp()` (after the `userBadge` line), hide the entry for non-superusers — the backend is still the authority, this only avoids showing a link that 403s:

```javascript
  const usageNav = document.getElementById('navUsageReport');
  if (usageNav) usageNav.classList.toggle('hidden', !(currentUser && currentUser.is_superuser));
```

- [ ] **Step 3: Write the statistics page**

Create `app/admin/js/usage_report.js`:

```javascript
/**
 * Oracle API Hub — API Usage Report
 *
 * Statistics over api_hub_audit_logs (who called which API, on which data
 * source, how often) plus a drill-down to the individual requests and the
 * parameters they were called with. Superuser-only; the backend enforces it.
 *
 * Lives outside app.js because that file is already large and this section is
 * self-contained. It relies on app.js globals: api(), esc(), toast(),
 * currentUser, syntaxHighlight(), rawJsonBlock-style copy helpers.
 */

/* ── State ────────────────────────────────────────────────────────────── */
const UR_ACTION_TYPES = ['query', 'procedure', 'write', 'definition', 'report'];
const UR_GROUPS = [
  { key: 'user', label: 'User' },
  { key: 'db', label: 'DW' },
  { key: 'api', label: 'API' },
  { key: 'day', label: 'Day' },
];

const urState = {
  dateFrom: '',
  dateTo: '',
  principal: '',
  dbAlias: '',
  action: '',
  actionTypes: ['query', 'procedure', 'write'],
  groupBy: ['user', 'db', 'api'],
  sort: '-calls',
  page: 1,
  pageSize: 50,
};

function urDefaultRange() {
  const end = new Date();
  const start = new Date(end.getTime() - 29 * 86400000);
  const iso = d => d.toISOString().slice(0, 10);
  return { from: iso(start), to: iso(end) };
}

// Shared section header for the statistics and detail views, which are two
// pages under one sidebar entry (same approach as enterSqlDefView).
function enterUsageView(title) {
  document.querySelectorAll('.sidebar-nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.page === 'usage-report'));
  document.getElementById('pageTitle').textContent = title;
  const area = document.getElementById('contentArea');
  area.scrollTop = 0;
  return area;
}

function urQueryString(extra = {}) {
  const params = new URLSearchParams();
  params.set('date_from', urState.dateFrom);
  params.set('date_to', urState.dateTo);
  if (urState.principal) params.set('principal', urState.principal);
  if (urState.dbAlias) params.set('db_alias', urState.dbAlias);
  if (urState.action) params.set('action', urState.action);
  // Always explicit: an empty string means "no action_type filter", which is
  // different from omitting the parameter (that would re-apply the default).
  params.set('action_type', urState.actionTypes.join(','));
  Object.entries(extra).forEach(([k, v]) => params.set(k, v));
  return params.toString();
}

/* ── Statistics page ─────────────────────────────────────────────────── */
async function renderUsageReport() {
  const area = enterUsageView('API Usage Report');
  if (!urState.dateFrom) {
    const { from, to } = urDefaultRange();
    urState.dateFrom = from;
    urState.dateTo = to;
  }
  area.innerHTML = urFilterBar() + '<div id="urResults"><div style="text-align:center;padding:40px"><div class="spinner"></div></div></div>';
  urBindFilterEvents();
  await urLoadFilterOptions();
  await urLoadStats();
}

function urFilterBar() {
  return `
    <div class="card">
      <div class="card-body">
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end">
          <div>
            <label class="text-sm">From</label><br>
            <input type="date" id="urFrom" value="${esc(urState.dateFrom)}">
          </div>
          <div>
            <label class="text-sm">To</label><br>
            <input type="date" id="urTo" value="${esc(urState.dateTo)}">
          </div>
          <div>
            <label class="text-sm">User</label><br>
            <select id="urPrincipal"><option value="">All</option></select>
          </div>
          <div>
            <label class="text-sm">DW</label><br>
            <select id="urDbAlias"><option value="">All</option></select>
          </div>
          <div>
            <label class="text-sm">API</label><br>
            <select id="urAction"><option value="">All</option></select>
          </div>
          <div>
            <button class="btn btn-primary btn-sm" id="urApply">Apply</button>
            <button class="btn btn-outline btn-sm" id="urDetail">Detail &rsaquo;</button>
          </div>
        </div>
        <div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:20px">
          <div>
            <span class="text-sm"><strong>Type</strong></span>
            ${UR_ACTION_TYPES.map(t => `
              <label class="text-sm" style="margin-left:8px">
                <input type="checkbox" class="ur-type" value="${t}"
                  ${urState.actionTypes.includes(t) ? 'checked' : ''}> ${t}
              </label>`).join('')}
          </div>
          <div>
            <span class="text-sm"><strong>Group by</strong></span>
            ${UR_GROUPS.map(g => `
              <label class="text-sm" style="margin-left:8px">
                <input type="checkbox" class="ur-group" value="${g.key}"
                  ${urState.groupBy.includes(g.key) ? 'checked' : ''}> ${esc(g.label)}
              </label>`).join('')}
          </div>
        </div>
      </div>
    </div>`;
}

function urReadFilterInputs() {
  urState.dateFrom = document.getElementById('urFrom').value;
  urState.dateTo = document.getElementById('urTo').value;
  urState.principal = document.getElementById('urPrincipal').value;
  urState.dbAlias = document.getElementById('urDbAlias').value;
  urState.action = document.getElementById('urAction').value;
  urState.actionTypes = [...document.querySelectorAll('.ur-type:checked')].map(el => el.value);
  urState.groupBy = [...document.querySelectorAll('.ur-group:checked')].map(el => el.value);
  urState.page = 1;
}

function urBindFilterEvents() {
  document.getElementById('urApply').addEventListener('click', () => {
    urReadFilterInputs();
    urLoadStats();
  });
  document.getElementById('urDetail').addEventListener('click', () => {
    urReadFilterInputs();
    renderUsageDetail({});
  });
}

async function urLoadFilterOptions() {
  let options = { principals: [], db_aliases: [], actions: [] };
  try {
    options = await api('GET', `/audit/filters?date_from=${urState.dateFrom}&date_to=${urState.dateTo}`);
  } catch (err) {
    toast(err.message, 'error');
  }
  const fill = (id, values, selected) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = '<option value="">All</option>' +
      values.map(v => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(v)}</option>`).join('');
  };
  fill('urPrincipal', options.principals, urState.principal);
  fill('urDbAlias', options.db_aliases, urState.dbAlias);
  fill('urAction', options.actions, urState.action);
}

const UR_METRIC_COLUMNS = [
  { key: 'calls', label: 'Calls' },
  { key: 'success', label: 'Success' },
  { key: 'failed', label: 'Failed' },
  { key: 'avg_duration_ms', label: 'Avg ms' },
  { key: 'max_duration_ms', label: 'Max ms' },
  { key: 'total_rows', label: 'Rows' },
  { key: 'last_used_at', label: 'Last used' },
];

function urCell(item, key) {
  if (key === 'last_used_at') return esc((item.last_used_at || '—').replace('T', ' ').slice(0, 19));
  return esc(item[key] == null ? '—' : item[key]);
}

async function urLoadStats() {
  const results = document.getElementById('urResults');
  results.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';
  let data;
  try {
    data = await api('GET', `/audit/usage?${urQueryString({
      group_by: urState.groupBy.join(','), sort: urState.sort,
      page: urState.page, page_size: urState.pageSize,
    })}`);
  } catch (err) {
    results.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
    return;
  }

  const dims = UR_GROUPS.filter(g => urState.groupBy.includes(g.key));
  const dimKey = { user: 'principal', db: 'db_alias', api: 'action', day: 'day' };
  const pages = Math.max(1, Math.ceil(data.total_groups / urState.pageSize));

  results.innerHTML = `
    <div class="card">
      <div class="card-header">
        <h2>Usage — ${data.total_groups} group(s)</h2>
        <span class="text-sm">Total ${data.totals.calls} call(s), ${data.totals.failed} failed,
          avg ${data.totals.avg_duration_ms} ms, ${data.totals.total_rows} row(s)</span>
      </div>
      <div class="card-body no-pad">
        <table class="data-table"><thead><tr>
          ${dims.map(d => `<th>${esc(d.label)}</th>`).join('')}
          ${UR_METRIC_COLUMNS.map(c => `<th><a href="#" class="ur-sort" data-col="${c.key}">${esc(c.label)}</a></th>`).join('')}
          <th></th>
        </tr></thead>
        <tbody>${data.items.length === 0
          ? `<tr><td colspan="${dims.length + UR_METRIC_COLUMNS.length + 1}">No usage in this range.</td></tr>`
          : data.items.map((item, i) => `<tr>
              ${dims.map(d => `<td>${esc(item[dimKey[d.key]] == null ? '—' : item[dimKey[d.key]])}</td>`).join('')}
              ${UR_METRIC_COLUMNS.map(c => `<td>${urCell(item, c.key)}</td>`).join('')}
              <td><button class="btn btn-sm btn-outline ur-drill" data-index="${i}">Detail</button></td>
            </tr>`).join('')}</tbody></table>
      </div>
      <div class="card-body">
        <span class="text-sm">Page ${data.page} / ${pages}</span>
        <button class="btn btn-sm btn-outline" id="urPrev" ${data.page <= 1 ? 'disabled' : ''}>Prev</button>
        <button class="btn btn-sm btn-outline" id="urNext" ${data.page >= pages ? 'disabled' : ''}>Next</button>
      </div>
    </div>`;

  window._urItems = data.items;
  results.querySelectorAll('.ur-sort').forEach(a => a.addEventListener('click', e => {
    e.preventDefault();
    const col = a.dataset.col;
    urState.sort = urState.sort === `-${col}` ? col : `-${col}`;
    urState.page = 1;
    urLoadStats();
  }));
  results.querySelectorAll('.ur-drill').forEach(b => b.addEventListener('click', () => {
    const item = window._urItems[Number(b.dataset.index)];
    renderUsageDetail({
      principal: item.principal || '', dbAlias: item.db_alias || '',
      action: item.action || '', day: item.day || '',
    });
  }));
  const prev = document.getElementById('urPrev');
  const next = document.getElementById('urNext');
  if (prev) prev.addEventListener('click', () => { urState.page -= 1; urLoadStats(); });
  if (next) next.addEventListener('click', () => { urState.page += 1; urLoadStats(); });
}
```

- [ ] **Step 4: Verify manually**

Run the app (`uvicorn app.main:app --port 8000`), log in at `/admin` as a superuser, hard-refresh, open **API Usage Report**. Expected: the filter bar renders, the dropdowns populate, and the table shows groups (the page's own `/audit/*` calls are recorded as `action_type=report`, so tick **report** to see rows immediately on a fresh database).

`renderUsageDetail` does not exist yet — the Detail buttons will throw until Task 8. That is expected at this checkpoint.

- [ ] **Step 5: Commit**

```bash
git add app/admin/index.html app/admin/js/app.js app/admin/js/usage_report.js
git commit -m "feat(admin-ui): add API usage report statistics page"
```

---

### Task 8: Admin UI — detail view

**Files:**
- Modify: `app/admin/js/usage_report.js` (append)

**Interfaces:**
- Consumes: `GET /audit/logs`, `GET /audit/logs/{id}` (Task 6); `enterUsageView()`, `urState`, `urQueryString()` (Task 7); `syntaxHighlight()`, `registerCopyPayload()`/`copyPayload()` from `app.js`.
- Produces: `renderUsageDetail(scope)` — called by the drill-down buttons wired in Task 7.

- [ ] **Step 1: Append the detail view**

Add to the end of `app/admin/js/usage_report.js`:

```javascript
/* ── Detail view ─────────────────────────────────────────────────────── */
// scope narrows the statistics filters to one group's values. `day` pins the
// range to a single date so a "Day" group drills into just that day.
const urDetailState = { scope: {}, page: 1, pageSize: 25 };

async function renderUsageDetail(scope) {
  urDetailState.scope = scope || {};
  urDetailState.page = 1;
  const area = enterUsageView('API Usage Detail');
  area.innerHTML = `
    <div class="card">
      <div class="card-body">
        <button class="btn btn-sm btn-outline" id="urBack">&lsaquo; Back to statistics</button>
        <span style="margin-left:12px">${urScopeChips()}</span>
      </div>
    </div>
    <div id="urDetailResults"><div style="text-align:center;padding:40px"><div class="spinner"></div></div></div>`;
  document.getElementById('urBack').addEventListener('click', renderUsageReport);
  await urLoadDetail();
}

function urScopeChips() {
  const s = urDetailState.scope;
  const chips = [
    ['Date', s.day || `${urState.dateFrom} ~ ${urState.dateTo}`],
    ['User', s.principal || urState.principal || 'All'],
    ['DW', s.dbAlias || urState.dbAlias || 'All'],
    ['API', s.action || urState.action || 'All'],
    ['Type', urState.actionTypes.join(', ') || 'All'],
  ];
  return chips.map(([label, value]) => `<span class="tag">${esc(label)}: ${esc(value)}</span>`).join(' ');
}

function urDetailQueryString() {
  const s = urDetailState.scope;
  const params = new URLSearchParams(urQueryString({
    page: urDetailState.page, page_size: urDetailState.pageSize,
  }));
  if (s.day) { params.set('date_from', s.day); params.set('date_to', s.day); }
  if (s.principal) params.set('principal', s.principal);
  if (s.dbAlias) params.set('db_alias', s.dbAlias);
  if (s.action) params.set('action', s.action);
  return params.toString();
}

async function urLoadDetail() {
  const results = document.getElementById('urDetailResults');
  results.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';
  let data;
  try {
    data = await api('GET', `/audit/logs?${urDetailQueryString()}`);
  } catch (err) {
    results.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
    return;
  }

  const pages = Math.max(1, Math.ceil(data.total / urDetailState.pageSize));
  results.innerHTML = `
    <div class="card">
      <div class="card-header"><h2>Detail — ${data.total} request(s)</h2></div>
      <div class="card-body no-pad">
        <table class="data-table"><thead><tr>
          <th>Time</th><th>User</th><th>Source IP</th><th>DW</th><th>Type</th>
          <th>API</th><th>Status</th><th>Duration</th><th>Rows</th><th></th>
        </tr></thead>
        <tbody>${data.items.length === 0
          ? '<tr><td colspan="10">No requests match these filters.</td></tr>'
          : data.items.map(item => urDetailRows(item)).join('')}</tbody></table>
      </div>
      <div class="card-body">
        <span class="text-sm">Page ${data.page} / ${pages}</span>
        <button class="btn btn-sm btn-outline" id="urdPrev" ${data.page <= 1 ? 'disabled' : ''}>Prev</button>
        <button class="btn btn-sm btn-outline" id="urdNext" ${data.page >= pages ? 'disabled' : ''}>Next</button>
      </div>
    </div>`;

  window._urLogs = {};
  data.items.forEach(item => { window._urLogs[item.id] = item; });
  results.querySelectorAll('.ur-expand').forEach(b =>
    b.addEventListener('click', () => urToggleParams(Number(b.dataset.id))));
  const prev = document.getElementById('urdPrev');
  const next = document.getElementById('urdNext');
  if (prev) prev.addEventListener('click', () => { urDetailState.page -= 1; urLoadDetail(); });
  if (next) next.addEventListener('click', () => { urDetailState.page += 1; urLoadDetail(); });
}

function urDetailRows(item) {
  const failed = item.status_code >= 400;
  return `
    <tr>
      <td class="text-sm">${esc((item.ts || '').replace('T', ' ').slice(0, 19))}</td>
      <td>${esc(item.principal)}${item.api_key_name ? ` <span class="tag">${esc(item.api_key_name)}</span>` : ''}</td>
      <td class="text-sm">${esc(item.client_ip)}</td>
      <td>${esc(item.db_alias || '—')}</td>
      <td><span class="tag">${esc(item.action_type || '—')}</span></td>
      <td><strong>${esc(item.action || item.path)}</strong></td>
      <td${failed ? ' style="color:var(--danger)"' : ''}>${esc(item.status_code)}</td>
      <td>${esc(item.duration_ms)} ms</td>
      <td>${esc(item.result_rows == null ? '—' : item.result_rows)}</td>
      <td><button class="btn btn-sm btn-outline ur-expand" data-id="${item.id}">&#9662;</button></td>
    </tr>
    <tr id="urParams${item.id}" class="hidden"><td colspan="10"></td></tr>`;
}

// Params for oversized payloads are not in the list response (the API omits
// them past AUDIT_PARAMS_INLINE_LIMIT), so fetch that row on demand.
async function urToggleParams(id) {
  const row = document.getElementById(`urParams${id}`);
  if (!row) return;
  if (!row.classList.contains('hidden')) { row.classList.add('hidden'); return; }
  row.classList.remove('hidden');
  const cell = row.querySelector('td');
  let item = window._urLogs[id];
  if (item.params_omitted) {
    cell.innerHTML = '<div class="spinner"></div>';
    try {
      item = await api('GET', `/audit/logs/${id}`);
      window._urLogs[id] = item;
    } catch (err) {
      cell.innerHTML = `<p>Error: ${esc(err.message)}</p>`;
      return;
    }
  }
  if (item.params == null) {
    cell.innerHTML = '<p class="text-sm">No parameters recorded for this request.</p>';
    return;
  }
  const json = JSON.stringify(item.params, null, 2);
  registerCopyPayload(`urParams-${id}`, json);
  cell.innerHTML = `
    <div class="qc-code-toolbar" style="margin-bottom:4px">
      <span class="text-sm">Request parameters</span>
      <button class="btn btn-outline btn-sm" onclick="copyPayload('urParams-${id}','Params JSON')">&#128203; Copy JSON</button>
    </div>
    <pre>${syntaxHighlight(json)}</pre>`;
}
```

- [ ] **Step 2: Verify manually**

Hard-refresh `/admin`, open **API Usage Report**, tick **report** under Type, Apply, then click a row's **Detail**. Expected: the detail table lists individual requests; the `▾` button expands a row and shows either the params JSON with a working Copy button or "No parameters recorded" (the `/audit/*` rows record no params).

- [ ] **Step 3: Commit**

```bash
git add app/admin/js/usage_report.js
git commit -m "feat(admin-ui): add usage report detail view with per-request params"
```

---

### Task 9: End-to-end coverage

**Files:**
- Create: `tests/e2e/usage-report.spec.js`
- Modify: `tests/e2e/README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–8, against the real Postgres the e2e harness starts.
- Produces: the only test that verifies the aggregation numbers against a live database.

**Approach:** the harness has no Oracle, so `POST /{db}/queries/execute` fails with a 500 — which is exactly what we want. Failed requests are audited by design, so the call seeds a real `action_type=query` row carrying `params`, and the report must show it as a failed call.

- [ ] **Step 1: Write the spec**

Create `tests/e2e/usage-report.spec.js`:

```javascript
const { test, expect } = require('@playwright/test');

const ADMIN = { user: 'admin', pass: 'changeme' };

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', ADMIN.user);
  await page.fill('#loginPass', ADMIN.pass);
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();
}

// Seeds one audited query execution. There is no Oracle in the harness, so
// this 500s — and that is the point: failed calls are audited too, so the
// row lands with action_type=query and its params intact.
async function seedQueryCall(page, params) {
  const token = await page.evaluate(() => localStorage.getItem('hub_token'));
  const res = await page.request.post('/api/v1/default/queries/execute', {
    headers: { Authorization: `Bearer ${token}` },
    data: { query_name: 'employees', params },
    failOnStatusCode: false,
  });
  expect(res.status()).toBeGreaterThanOrEqual(400);
}

test('usage report aggregates calls and drills into request params', async ({ page }) => {
  await login(page);
  await seedQueryCall(page, { dept: 'E2E-IT' });
  await seedQueryCall(page, { dept: 'E2E-HR' });

  await page.click('.sidebar-nav a[data-page="usage-report"]');
  await expect(page.locator('#urFrom')).toBeVisible();

  // Default filters already cover query/procedure/write over the last 30 days.
  // The audit row is written as a background task, so retry the Apply until
  // the aggregation catches up.
  await expect(async () => {
    await page.click('#urApply');
    await expect(page.locator('.data-table tbody tr', { hasText: 'employees' }).first()).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 15000 });

  const row = page.locator('.data-table tbody tr', { hasText: 'employees' }).first();
  await expect(row).toContainText('admin');
  // Two failed calls, grouped by user + DW + API.
  await expect(row.locator('td').nth(3)).toHaveText('2');   // Calls
  await expect(row.locator('td').nth(4)).toHaveText('0');   // Success
  await expect(row.locator('td').nth(5)).toHaveText('2');   // Failed

  await row.locator('.ur-drill').click();
  await expect(page.locator('#urBack')).toBeVisible();
  await expect(page.locator('.data-table tbody tr').first()).toContainText('employees');

  // Expand the newest request and confirm the recorded params are visible.
  await page.locator('.ur-expand').first().click();
  const pre = page.locator('#urDetailResults pre').first();
  await expect(pre).toBeVisible();
  await expect(pre).toContainText('E2E-HR');
  await expect(pre).toContainText('fetch_mode');

  await page.locator('button', { hasText: 'Copy JSON' }).first().click();
  await expect(page.locator('.toast')).toContainText('copied');
});

test('grouping by day collapses the API dimension', async ({ page }) => {
  await login(page);
  await seedQueryCall(page, { dept: 'E2E-DAY' });

  await page.click('.sidebar-nav a[data-page="usage-report"]');
  await expect(page.locator('#urFrom')).toBeVisible();
  await page.uncheck('.ur-group[value="api"]');
  await page.uncheck('.ur-group[value="db"]');
  await page.check('.ur-group[value="day"]');

  await expect(async () => {
    await page.click('#urApply');
    await expect(page.locator('.data-table thead th', { hasText: 'Day' })).toBeVisible({ timeout: 2000 });
    await expect(page.locator('.data-table tbody tr', { hasText: 'admin' }).first()).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 15000 });

  await expect(page.locator('.data-table thead th', { hasText: 'API' })).toHaveCount(0);
});

test('non-superuser never sees the report link', async ({ page }) => {
  await login(page);
  const token = await page.evaluate(() => localStorage.getItem('hub_token'));
  await page.request.post('/api/v1/auth/users', {
    headers: { Authorization: `Bearer ${token}` },
    data: { username: 'e2e_reader', password: 'reader-pass-1', full_name: 'Reader', is_superuser: false },
    failOnStatusCode: false,
  });

  await page.evaluate(() => localStorage.removeItem('hub_token'));
  await page.goto('/admin');
  await page.fill('#loginUser', 'e2e_reader');
  await page.fill('#loginPass', 'reader-pass-1');
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();
  await expect(page.locator('#navUsageReport')).toBeHidden();
});
```

- [ ] **Step 2: Run the e2e suite**

Run: `cd tests/e2e && npm test`
Expected: PASS. If the third test's user-creation endpoint path differs, correct it against `app/api/v1/auth.py` — do not weaken the assertion. If the metric column indices shift because Task 7's column order changed, fix the indices, not the expected values.

- [ ] **Step 3: Note the new spec in the e2e README**

Add a line to `tests/e2e/README.md` describing `usage-report.spec.js`: it seeds audited (deliberately failing) query executions and asserts the aggregation, drill-down, params expansion and the non-superuser nav gate. Match the file's existing tone and structure.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/usage-report.spec.js tests/e2e/README.md
git commit -m "test(e2e): cover usage report aggregation, drill-down and params expansion"
```

---

### Task 10: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/system_specification.md`
- Modify: `app/admin/CLAUDE.md`
- Modify: `CLAUDE.md` and `AGENTS.md`

**Interfaces:**
- Consumes: the shipped behaviour from Tasks 1–9.
- Produces: nothing consumed by code.

- [ ] **Step 1: Update `app/admin/CLAUDE.md`**

The file currently states the SPA is `index.html` + one `css/style.css` + one `js/app.js`. Correct it: there are now two JS files, `js/app.js` (shell, shared helpers, most sections) and `js/usage_report.js` (the API Usage Report section, which relies on `app.js` globals and must load after it). Note that both are served from the `/admin/js` StaticFiles mount, so adding a file needs no `main.py` change. Add the API Usage Report to the list of sections using the full-page (non-modal) list→detail pattern alongside SQL Definitions.

- [ ] **Step 2: Update `README.md`**

Add the report to the feature list and document the new operational surface:

- `GET /api/v1/audit/usage`, `/audit/logs`, `/audit/logs/{id}`, `/audit/filters` — superuser only
- Admin UI: **API Usage Report** under Operations
- `AUDIT_RETENTION_DAYS` (default 365) and `AUDIT_PARAMS_INLINE_LIMIT` (default 4096) in the settings table
- `python -m scripts.purge_audit_logs [--days N] [--dry-run]` in the commands section
- A warning that `params` is recorded unmasked, so audit rows can contain any value a caller passed as a bind parameter, and database backups carry it

- [ ] **Step 3: Update `docs/system_specification.md`**

Follow the file's existing structure and add:

- the three new `api_hub_audit_logs` columns and the four composite indexes in the schema section
- the `action_type` taxonomy and which endpoint sets which value
- the four report endpoints with their parameters, defaults and limits (`page_size` ≤ 200 for usage, ≤ 100 for logs)
- the metric definitions: `success` = `status_code < 400`, `failed` = `>= 400`, `total_rows` = sum of `result_rows` with `NULL` as 0
- the retention/purge story and the unmasked-params risk

- [ ] **Step 4: Update `CLAUDE.md` and mirror to `AGENTS.md`**

In the **Request Pipeline — Auth, Rate Limiting, Audit** section, extend the `AuditMiddleware` bullet: routers now record `action_type` / `params` / `result_rows` via `record_audit()` because the middleware cannot read a consumed request body, and this data backs the superuser-only usage report at `/api/v1/audit/*`. Note that `params` is stored unmasked and that `AUDIT_RETENTION_DAYS` + `scripts/purge_audit_logs` are what bound table growth. Mirror the same edit into `AGENTS.md`, per the existing convention.

- [ ] **Step 5: Verify the docs match reality**

Run: `pytest -q && python -c "from app.main import app; print(sorted(r.path for r in app.routes if '/audit' in r.path))"`
Expected: tests PASS and the printed paths match exactly what the docs claim.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/system_specification.md app/admin/CLAUDE.md CLAUDE.md AGENTS.md
git commit -m "docs: document the API usage report, audit columns and retention purge"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `AuditLog` new columns | 1 |
| Schema evolution via `init_db.py` | 1 |
| Four composite indexes | 1 |
| `AUDIT_RETENTION_DAYS` + purge script | 1 |
| `record_audit()` helper | 2 |
| Instrument queries/execute + definitions CRUD | 3 |
| Instrument procedures + writes | 4 |
| `request` → `payload` rename | 4 |
| `report` action_type on `/audit/*` | 6 |
| `GET /audit/usage` with filters/group_by/sort/paging | 5, 6 |
| Metric definitions (`success` < 400 etc.) | 5 |
| `GET /audit/logs` + inline-limit omission | 5, 6 |
| `GET /audit/logs/{id}` | 5, 6 |
| `GET /audit/filters` | 5, 6 |
| Superuser-only | 6 (dep), 7 (nav hiding), 9 (e2e) |
| Statistics page + Type/Group-by checkboxes | 7 |
| Detail view + params expand + copy JSON | 8 |
| Separate `usage_report.js` | 7, 10 |
| Error handling (400/403/404/empty) | 5, 6 |
| Test matrix | 1, 2, 3, 4, 5, 6, 9 |
| Docs (README, spec, admin CLAUDE.md) | 10 |
| Known risks documented | 10 |

**Deviation from the spec's test matrix, stated deliberately:** the spec listed `tests/test_audit_report_service.py` as covering numeric aggregation correctness. `pytest` has no live PostgreSQL in this project, and the aggregation depends on PostgreSQL-only constructs (`JSONB`, `COUNT(*) FILTER`, `date()`). So that file covers parsing, validation, compiled-SQL shape and `_log_to_dict`, and the numeric assertions live in `tests/e2e/usage-report.spec.js` where a real Postgres exists. Coverage is preserved; its location moved.

**Type consistency:** `record_audit` keeps one signature across Tasks 2/3/4/6. `svc.*` names in Task 6 match every function Task 5 defines. `GROUP_KEYS` values (`principal`/`db_alias`/`action`/`day`) match `UsageItem`'s fields and the UI's `dimKey` map. `AuditLogItem` fields match `_log_to_dict`'s keys exactly. `enterUsageView`/`urState`/`urQueryString` are defined in Task 7 and used in Task 8.
