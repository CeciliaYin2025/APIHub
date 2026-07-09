# SQL Definition `order_by` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured `order_by` field to SQL definitions so the Hub injects ORDER BY in the wrapper layer, working on MSSQL (where ORDER BY is illegal in a derived table) and giving stable, consistent pagination ordering across Oracle/PostgreSQL/MSSQL.

**Architecture:** `order_by` is a whitelist-validated `list[{column, direction}]` stored on each definition (JSONB). At execution the service builds a safe `"COL ASC, COL2 DESC"` string (regex + enum re-validated) and passes it to each dialect's `wrap_paginated/wrap_top_n/wrap_one/wrap_all`, which inject it at the engine-correct position. Empty `order_by` → wrappers behave exactly as today (full backward compatibility).

**Tech Stack:** FastAPI, SQLAlchemy ORM (PostgreSQL platform store, JSONB), Pydantic (Literal + regex validation), pytest (pure unit tests asserting generated SQL strings + fake-DB CRUD).

## Global Constraints

- Python 3.10+; line length 120; must pass `ruff check app/ tests/` and `ruff format app/ tests/`.
- Order columns **cannot be bind parameters** — they are validated against `^[A-Za-z_][A-Za-z0-9_]{0,127}$` and string-built; `direction` is `Literal["ASC","DESC"]`. This is the only injection surface — validate at BOTH the Pydantic layer and the execution-time builder (defense against YAML/DB rows bypassing Pydantic).
- `order_by` operates on the definition's **output column names** (the ORDER BY is applied to the derived table `sub_`). No dotted names, quotes, whitespace, expressions, or `NULLS FIRST/LAST`.
- **Fully backward compatible:** an empty/absent `order_by` (default) must produce byte-identical wrapper SQL to today. Every wrap function keeps `order_by: str | None = None` defaulting to None → current behavior.
- Platform store is always PostgreSQL; `order_by` persists as a JSONB list of `{"column": str, "direction": "ASC"|"DESC"}`.
- Non-goals (do NOT build): request-time/dynamic ordering, `NULLS FIRST/LAST`, sort expressions.

---

### Task 1: `OrderBySchema` + schema fields

**Files:**
- Modify: `app/schemas/sql_definition.py`
- Test: `tests/test_order_by_schema.py`

**Interfaces:**
- Produces: `OrderBySchema(column: str, direction: Literal["ASC","DESC"]="ASC")`; `SqlDefinitionCreate/Update/Out` gain `order_by`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_order_by_schema.py
"""Validation of the OrderBySchema and order_by on definition schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.sql_definition import OrderBySchema, SqlDefinitionCreate


def test_valid_order_by():
    o = OrderBySchema(column="LastUpdateTime", direction="DESC")
    assert o.column == "LastUpdateTime"
    assert o.direction == "DESC"


def test_direction_defaults_to_asc():
    assert OrderBySchema(column="name").direction == "ASC"


def test_invalid_direction_rejected():
    with pytest.raises(ValidationError):
        OrderBySchema(column="name", direction="SIDEWAYS")


@pytest.mark.parametrize("bad", ["a b", "a;b", "a.b", "1col", "col'", "a-b", ""])
def test_invalid_column_rejected(bad):
    with pytest.raises(ValidationError):
        OrderBySchema(column=bad)


def test_definition_create_defaults_empty_order_by():
    d = SqlDefinitionCreate(name="q", sql="SELECT 1")
    assert d.order_by == []


def test_definition_create_accepts_order_by():
    d = SqlDefinitionCreate(
        name="q", sql="SELECT 1",
        order_by=[{"column": "a", "direction": "DESC"}, {"column": "b"}],
    )
    assert d.order_by[0].column == "a"
    assert d.order_by[1].direction == "ASC"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_by_schema.py -v`
Expected: FAIL — `cannot import name 'OrderBySchema'`

- [ ] **Step 3: Implement**

In `app/schemas/sql_definition.py`, update the import line and add the schema + fields:

```python
from typing import Literal

from pydantic import BaseModel, Field
```

Add after `ParamDefSchema`:

```python
class OrderBySchema(BaseModel):
    column: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    direction: Literal["ASC", "DESC"] = "ASC"
```

Add `order_by` to each definition schema:
- `SqlDefinitionCreate`: `order_by: list[OrderBySchema] = Field(default_factory=list)`
- `SqlDefinitionUpdate`: `order_by: list[OrderBySchema] | None = None`
- `SqlDefinitionOut`: `order_by: list[OrderBySchema] = Field(default_factory=list)`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_order_by_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/sql_definition.py tests/test_order_by_schema.py
git commit -m "feat(order-by): OrderBySchema + order_by on definition schemas"
```

---

### Task 2: `_build_order_by` execution-time builder (injection-safe)

**Files:**
- Modify: `app/services/query_service.py`
- Test: `tests/test_build_order_by.py`

**Interfaces:**
- Produces: `_build_order_by(definition) -> str | None` — reads `definition.order_by` (list of dicts), re-validates each column (regex) + direction (ASC/DESC), returns `"COL DIR, COL2 DIR2"` or `None` for empty. Raises `ValueError` on invalid.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_order_by.py
"""Execution-time order_by string builder — re-validates against injection."""

import pytest

from app.services.query_service import _build_order_by


class _Def:
    def __init__(self, order_by):
        self.order_by = order_by


def test_empty_returns_none():
    assert _build_order_by(_Def([])) is None
    assert _build_order_by(_Def(None)) is None


def test_builds_string():
    d = _Def([{"column": "PROJECT_ID", "direction": "ASC"},
              {"column": "NAME", "direction": "DESC"}])
    assert _build_order_by(d) == "PROJECT_ID ASC, NAME DESC"


def test_direction_defaulted_and_uppercased():
    assert _build_order_by(_Def([{"column": "a"}])) == "a ASC"
    assert _build_order_by(_Def([{"column": "a", "direction": "desc"}])) == "a DESC"


@pytest.mark.parametrize("bad", ["a; DROP TABLE t", "a b", "a.b", "1a", "col'", "a-b", ""])
def test_invalid_column_raises(bad):
    with pytest.raises(ValueError):
        _build_order_by(_Def([{"column": bad, "direction": "ASC"}]))


def test_invalid_direction_raises():
    with pytest.raises(ValueError):
        _build_order_by(_Def([{"column": "a", "direction": "SIDEWAYS"}]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_build_order_by.py -v`
Expected: FAIL — `cannot import name '_build_order_by'`

- [ ] **Step 3: Implement**

In `app/services/query_service.py`, add near the top (after imports):

```python
import re

_ORDER_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _build_order_by(definition) -> str | None:
    """Build a safe ORDER BY clause body from a definition's order_by list.

    Re-validates each column against the identifier whitelist and direction
    against ASC/DESC (defense in depth vs YAML/DB rows that bypass Pydantic),
    then joins into "COL DIR, COL2 DIR2". Returns None for an empty list.
    Raises ValueError on any invalid column/direction.
    """
    items = getattr(definition, "order_by", None) or []
    parts: list[str] = []
    for item in items:
        column = item.get("column")
        direction = (item.get("direction") or "ASC").upper()
        if not column or not _ORDER_COLUMN_RE.match(column):
            raise ValueError(f"Invalid order_by column: {column!r}")
        if direction not in ("ASC", "DESC"):
            raise ValueError(f"Invalid order_by direction: {direction!r}")
        parts.append(f"{column} {direction}")
    return ", ".join(parts) if parts else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_build_order_by.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/query_service.py tests/test_build_order_by.py
git commit -m "feat(order-by): injection-safe _build_order_by execution builder"
```

---

### Task 3: Dialect layer — inject order_by (base + 3 dialects, atomic)

**Files:**
- Modify: `app/db/dialect.py`
- Modify: `app/db/dialects/mssql_dialect.py`
- Modify: `app/db/dialects/oracle_dialect.py`
- Modify: `app/db/dialects/postgresql_dialect.py`
- Test: `tests/test_dialect_order_by.py`

**Interfaces:**
- Consumes: order_by string from `_build_order_by`.
- Produces: `wrap_paginated/wrap_top_n/wrap_one(self, sql, order_by: str | None = None)` on all dialects; new concrete `Dialect.wrap_all(self, sql, order_by=None)`. All default `order_by=None` → identical to current SQL.

> This is one atomic task: changing the abstract signatures in `dialect.py` requires every dialect to match simultaneously, or the app breaks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dialect_order_by.py
"""Each dialect injects order_by at the engine-correct position; None = unchanged."""

from app.db.dialects.mssql_dialect import MssqlDialect
from app.db.dialects.oracle_dialect import OracleDialect
from app.db.dialects.postgresql_dialect import PostgresqlDialect

SQL = "SELECT a, b FROM t"


def _norm(s):
    return " ".join(s.split())


# ── backward compat: order_by=None reproduces existing SQL shape ──────────

def test_pg_paginated_no_order_unchanged():
    assert _norm(PostgresqlDialect().wrap_paginated(SQL)) == \
        _norm(f"SELECT * FROM ({SQL}) sub_ LIMIT :pag_limit OFFSET :pag_offset")


def test_mssql_paginated_no_order_uses_select_null():
    out = _norm(MssqlDialect().wrap_paginated(SQL))
    assert "ORDER BY (SELECT NULL)" in out


def test_oracle_paginated_no_order_has_no_inner_order():
    assert "ORDER BY" not in OracleDialect().wrap_paginated(SQL)


# ── order_by injected ─────────────────────────────────────────────────────

def test_pg_paginated_with_order():
    out = _norm(PostgresqlDialect().wrap_paginated(SQL, "NAME DESC"))
    assert _norm(f"SELECT * FROM ({SQL}) sub_ ORDER BY NAME DESC "
                 f"LIMIT :pag_limit OFFSET :pag_offset") == out


def test_pg_top_n_and_one_with_order():
    assert "ORDER BY NAME DESC" in PostgresqlDialect().wrap_top_n(SQL, "NAME DESC")
    assert "ORDER BY NAME DESC" in PostgresqlDialect().wrap_one(SQL, "NAME DESC")


def test_mssql_paginated_with_order_in_window():
    out = _norm(MssqlDialect().wrap_paginated(SQL, "NAME DESC"))
    assert "ROW_NUMBER() OVER (ORDER BY NAME DESC)" in out


def test_mssql_top_n_and_one_with_order():
    assert "ORDER BY NAME DESC" in MssqlDialect().wrap_top_n(SQL, "NAME DESC")
    assert "ORDER BY NAME DESC" in MssqlDialect().wrap_one(SQL, "NAME DESC")


def test_oracle_paginated_orders_before_rownum():
    out = _norm(OracleDialect().wrap_paginated(SQL, "NAME DESC"))
    # inner ordering applied before ROWNUM is assigned
    assert f"SELECT * FROM ({SQL}) ORDER BY NAME DESC" in out
    assert out.index("ORDER BY NAME DESC") < out.index("ROWNUM")


def test_oracle_top_n_and_one_with_order():
    assert "ORDER BY NAME DESC" in OracleDialect().wrap_top_n(SQL, "NAME DESC")
    assert "ORDER BY NAME DESC" in OracleDialect().wrap_one(SQL, "NAME DESC")


# ── wrap_all (base, shared by all dialects) ───────────────────────────────

def test_wrap_all_none_returns_sql_unchanged():
    assert PostgresqlDialect().wrap_all(SQL) == SQL


def test_wrap_all_with_order():
    assert _norm(PostgresqlDialect().wrap_all(SQL, "NAME DESC")) == \
        _norm(f"SELECT * FROM ({SQL}) sub_ ORDER BY NAME DESC")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dialect_order_by.py -v`
Expected: FAIL — `wrap_paginated() got an unexpected keyword argument 'order_by'` / `wrap_all` missing

- [ ] **Step 3a: Base dialect (`app/db/dialect.py`)**

Change the three abstract signatures to accept `order_by`:

```python
    @abc.abstractmethod
    def wrap_paginated(self, sql: str, order_by: str | None = None) -> str:
        """Wrap *sql* with engine-specific pagination (binds from ``pagination_binds``)."""

    @abc.abstractmethod
    def wrap_top_n(self, sql: str, order_by: str | None = None) -> str:
        """Wrap *sql* to return at most N rows (bind from ``top_n_binds``)."""

    @abc.abstractmethod
    def wrap_one(self, sql: str, order_by: str | None = None) -> str:
        """Wrap *sql* to return at most 1 row."""
```

Add a concrete `wrap_all` (near `wrap_count`):

```python
    def wrap_all(self, sql: str, order_by: str | None = None) -> str:
        """Return every row, optionally ordered. Outermost ORDER BY is legal on all engines."""
        if order_by is None:
            return sql
        return f"SELECT * FROM ({sql}) sub_ ORDER BY {order_by}"
```

- [ ] **Step 3b: MSSQL (`app/db/dialects/mssql_dialect.py`)**

```python
    def wrap_paginated(self, sql: str, order_by: str | None = None) -> str:
        order_clause = order_by if order_by else "(SELECT NULL)"
        return f"""
            SELECT * FROM (
                SELECT sub_.*, ROW_NUMBER() OVER (ORDER BY {order_clause}) AS rnum_
                FROM ({sql}) sub_
            ) pag_
            WHERE rnum_ > :pag_offset AND rnum_ <= :pag_max_row
        """

    def wrap_top_n(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT TOP (:pag_top_n) * FROM ({sql}) sub_{order}"

    def wrap_one(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT TOP 1 * FROM ({sql}) sub_{order}"
```

- [ ] **Step 3c: Oracle (`app/db/dialects/oracle_dialect.py`)**

```python
    def wrap_paginated(self, sql: str, order_by: str | None = None) -> str:
        inner = f"SELECT * FROM ({sql}) ORDER BY {order_by}" if order_by else sql
        return f"""
            SELECT * FROM (
                SELECT inner_.*, ROWNUM rnum_ FROM (
                    {inner}
                ) inner_
                WHERE ROWNUM <= :pag_max_row
            )
            WHERE rnum_ > :pag_min_row
        """

    def wrap_top_n(self, sql: str, order_by: str | None = None) -> str:
        inner = f"SELECT * FROM ({sql}) ORDER BY {order_by}" if order_by else sql
        return f"SELECT * FROM ({inner}) WHERE ROWNUM <= :pag_top_n"

    def wrap_one(self, sql: str, order_by: str | None = None) -> str:
        inner = f"SELECT * FROM ({sql}) ORDER BY {order_by}" if order_by else sql
        return f"SELECT * FROM ({inner}) WHERE ROWNUM <= 1"
```

- [ ] **Step 3d: PostgreSQL (`app/db/dialects/postgresql_dialect.py`)**

```python
    def wrap_paginated(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT * FROM ({sql}) sub_{order} LIMIT :pag_limit OFFSET :pag_offset"

    def wrap_top_n(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT * FROM ({sql}) sub_{order} LIMIT :pag_top_n"

    def wrap_one(self, sql: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return f"SELECT * FROM ({sql}) sub_{order} LIMIT 1"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dialect_order_by.py -v`
Then confirm no regression to existing dialect/query tests: `pytest tests/test_query_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db/dialect.py app/db/dialects/ tests/test_dialect_order_by.py
git commit -m "feat(order-by): dialects inject order_by; add wrap_all (backward compatible)"
```

---

### Task 4: ORM column + migration

**Files:**
- Modify: `app/models/sql_definition.py`
- Modify: `scripts/init_db.py`
- Test: `tests/test_order_by_model.py`

**Interfaces:**
- Produces: `SqlDefinitionModel.order_by` (JSONB, default list).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_order_by_model.py
"""SqlDefinitionModel exposes an order_by JSONB column."""

from app.models.sql_definition import SqlDefinitionModel


def test_model_has_order_by_column():
    row = SqlDefinitionModel(name="q", db_alias="default", sql="SELECT 1",
                             permission="query:q",
                             order_by=[{"column": "a", "direction": "DESC"}])
    assert row.order_by == [{"column": "a", "direction": "DESC"}]
    assert "order_by" in SqlDefinitionModel.__table__.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_by_model.py -v`
Expected: FAIL — `'order_by' is an invalid keyword argument for SqlDefinitionModel`

- [ ] **Step 3: Implement**

In `app/models/sql_definition.py`, add after the `params` column:

```python
    order_by = Column(JSONB, default=list)  # list of {"column": str, "direction": "ASC"|"DESC"}
```

In `scripts/init_db.py`, add another idempotent migration inside the existing `with engine.begin() as conn:` block (the one added for `is_service_account`), or a new one after `create_all`:

```python
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE api_hub_sql_definitions "
            "ADD COLUMN IF NOT EXISTS order_by jsonb DEFAULT '[]'::jsonb"
        ))
    print("Ensured api_hub_sql_definitions.order_by column.")
```

(If `from sqlalchemy import text` is already imported for the earlier migration, reuse it; otherwise add it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_order_by_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models/sql_definition.py scripts/init_db.py tests/test_order_by_model.py
git commit -m "feat(order-by): order_by JSONB column + idempotent migration"
```

---

### Task 5: Registry — SqlDefinition.order_by + loaders

**Files:**
- Modify: `app/services/sql_registry.py`
- Test: `tests/test_registry_order_by.py`

**Interfaces:**
- Consumes: `SqlDefinitionModel.order_by` (Task 4); YAML `order_by` key.
- Produces: `SqlDefinition.order_by: list[dict]` populated by both DB and YAML loaders.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry_order_by.py
"""SqlDefinition dataclass carries order_by; loaders populate it."""

from app.services.sql_registry import SqlDefinition


def test_dataclass_defaults_empty():
    d = SqlDefinition(name="q", description="", sql="SELECT 1", permission="query:q")
    assert d.order_by == []


def test_dataclass_accepts_order_by():
    d = SqlDefinition(name="q", description="", sql="SELECT 1", permission="query:q",
                      order_by=[{"column": "a", "direction": "DESC"}])
    assert d.order_by[0]["column"] == "a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_registry_order_by.py -v`
Expected: FAIL — `__init__() got an unexpected keyword argument 'order_by'`

- [ ] **Step 3: Implement**

In `app/services/sql_registry.py`:

Add the field to the dataclass (after `params`):

```python
    order_by: list[dict] = field(default_factory=list)
```

In `_load_from_db`, pass `order_by` when building `SqlDefinition`:

```python
                    defn = SqlDefinition(
                        name=row.name,
                        description=row.description or "",
                        sql=row.sql.strip(),
                        permission=row.permission or f"query:{row.name}",
                        db_alias=row.db_alias or "default",
                        params=params,
                        order_by=row.order_by or [],
                    )
```

In `_load_yaml_file`, pass `order_by` from the raw dict:

```python
        defn = SqlDefinition(
            name=raw["name"],
            description=raw.get("description", ""),
            sql=raw["sql"].strip(),
            permission=raw.get("permission", f"query:{raw['name']}"),
            db_alias=db_alias,
            params=params,
            order_by=raw.get("order_by") or [],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_registry_order_by.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/sql_registry.py tests/test_registry_order_by.py
git commit -m "feat(order-by): registry SqlDefinition.order_by + DB/YAML loaders"
```

---

### Task 6: Wire order_by into the query service fetch modes

**Files:**
- Modify: `app/services/query_service.py`
- Test: `tests/test_query_service_order_by.py`

**Interfaces:**
- Consumes: `_build_order_by` (Task 2); dialect wrap functions with `order_by` (Task 3); `definition.order_by` (Task 5).
- Produces: `execute_query` computes the order_by string once and threads it through all five fetch modes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query_service_order_by.py
"""execute_query passes the built order_by into the dialect wrap functions."""

from app.services import query_service
from app.services.sql_registry import SqlDefinition


class _Dialect:
    """Records the order_by argument each wrap call receives."""
    def __init__(self):
        self.calls = {}

    def wrap_count(self, sql):
        return f"SELECT COUNT(*) FROM ({sql}) c"

    def wrap_paginated(self, sql, order_by=None):
        self.calls["paginated"] = order_by
        return sql

    def wrap_all(self, sql, order_by=None):
        self.calls["all"] = order_by
        return sql

    def wrap_top_n(self, sql, order_by=None):
        self.calls["top_n"] = order_by
        return sql

    def wrap_one(self, sql, order_by=None):
        self.calls["one"] = order_by
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


class _Cursor:
    description = [("a",)]

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return (0,)

    def close(self):
        pass

    def __iter__(self):
        return iter([])


class _Conn:
    def cursor(self):
        return _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, dialect):
    monkeypatch.setattr(query_service, "get_dialect_for", lambda alias: dialect)
    monkeypatch.setattr(query_service, "get_connection", lambda alias: _Conn())


def _defn(order_by):
    return SqlDefinition(name="q", description="", sql="SELECT a FROM t",
                         permission="query:q", order_by=order_by)


def test_page_mode_passes_order_by(monkeypatch):
    d = _Dialect()
    _patch(monkeypatch, d)
    query_service.execute_query(_defn([{"column": "a", "direction": "DESC"}]), {},
                                fetch_mode="page")
    assert d.calls["paginated"] == "a DESC"


def test_all_mode_uses_wrap_all_with_order(monkeypatch):
    d = _Dialect()
    _patch(monkeypatch, d)
    query_service.execute_query(_defn([{"column": "a"}]), {}, fetch_mode="all")
    assert d.calls["all"] == "a ASC"


def test_empty_order_by_passes_none(monkeypatch):
    d = _Dialect()
    _patch(monkeypatch, d)
    query_service.execute_query(_defn([]), {}, fetch_mode="top_n")
    assert d.calls["top_n"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_query_service_order_by.py -v`
Expected: FAIL — `all` mode currently executes raw sql (no `wrap_all` call), and order_by isn't passed to `wrap_paginated`/`wrap_top_n`.

- [ ] **Step 3: Implement**

In `app/services/query_service.py`:

In `execute_query`, after `sql = _normalize_sql(definition.sql)`, compute:

```python
    order_by = _build_order_by(definition)
```

Pass `order_by` to each fetch call:

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
```

Update each fetch function to accept `order_by: str | None = None` and pass it to the dialect wrap:

- `_fetch_page`: add `order_by` param; `paginated_sql = dialect.wrap_paginated(sql, order_by)` (count unchanged).
- `_fetch_all`: add `order_by` param; replace the raw-`sql` data execution with `data_sql = dialect.wrap_all(sql, order_by)` and execute `data_sql` (count unchanged — still `dialect.wrap_count(sql)`).
- `_fetch_batch`: add `order_by` param; `paginated_sql = dialect.wrap_paginated(sql, order_by)`.
- `_fetch_top_n`: add `order_by` param; `top_sql = dialect.wrap_top_n(sql, order_by)`.
- `_fetch_one`: add `order_by` param; `top_sql = dialect.wrap_one(sql, order_by)`.

Concretely for `_fetch_all` (was executing `sql` directly):

```python
def _fetch_all(
    sql: str, binds: dict[str, Any], db_alias: str | None = None,
    order_by: str | None = None,
) -> dict[str, Any]:
    dialect = get_dialect_for(db_alias)
    count_sql = dialect.wrap_count(sql)
    data_sql = dialect.wrap_all(sql, order_by)

    with get_connection(db_alias) as conn:
        total = _get_count(conn, count_sql, binds, dialect)
        columns, data = _execute_and_map(conn, data_sql, binds, dialect)

    return {"columns": columns, "data": data, "total": total}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_query_service_order_by.py tests/test_query_service.py -v`
Expected: PASS (new tests pass; existing query_service tests unaffected because order_by defaults to None → wrap_all returns raw sql).

- [ ] **Step 5: Commit**

```bash
git add app/services/query_service.py tests/test_query_service_order_by.py
git commit -m "feat(order-by): thread order_by through all five fetch modes"
```

---

### Task 7: API CRUD + YAML import/export carry order_by

**Files:**
- Modify: `app/api/v1/queries.py`
- Test: `tests/test_definition_order_by_api.py`

**Interfaces:**
- Consumes: `SqlDefinitionCreate/Update/Out.order_by` (Task 1); `SqlDefinitionModel.order_by` (Task 4).
- Produces: create/update persist `order_by`; `_row_to_out` returns it; YAML import reads it; YAML export writes it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_definition_order_by_api.py
"""create/update definition persists order_by; _row_to_out returns it."""

from app.api.v1 import queries
from app.models.sql_definition import SqlDefinitionModel
from app.models.user import User
from app.schemas.sql_definition import SqlDefinitionCreate, SqlDefinitionUpdate


class _Query:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _DB:
    def __init__(self, existing=None):
        self._existing = existing
        self.added = []

    def query(self, _m):
        return _Query(self._existing)

    def add(self, o):
        self.added.append(o)

    def commit(self):
        pass

    def refresh(self, o):
        pass


def _admin():
    return User(username="admin", is_superuser=True, is_active=True)


def test_row_to_out_includes_order_by():
    row = SqlDefinitionModel(name="q", db_alias="default", sql="SELECT 1",
                             permission="query:q",
                             order_by=[{"column": "a", "direction": "DESC"}])
    out = queries._row_to_out(row)
    assert out["order_by"] == [{"column": "a", "direction": "DESC"}]


def test_create_persists_order_by(monkeypatch):
    monkeypatch.setattr(queries.sql_registry, "reload_from_db", lambda: None)
    db = _DB(existing=None)
    body = SqlDefinitionCreate(name="q", sql="SELECT 1",
                               order_by=[{"column": "a", "direction": "DESC"}])
    queries.create_definition(body, db="default", current_user=_admin(), pgdb=db)
    assert db.added[0].order_by == [{"column": "a", "direction": "DESC"}]


def test_update_replaces_order_by(monkeypatch):
    monkeypatch.setattr(queries.sql_registry, "reload_from_db", lambda: None)
    row = SqlDefinitionModel(name="q", db_alias="default", sql="SELECT 1",
                             permission="query:q", order_by=[])
    body = SqlDefinitionUpdate(order_by=[{"column": "b", "direction": "ASC"}])
    queries.update_definition("q", body, db="default", current_user=_admin(), pgdb=_DB(existing=row))
    assert row.order_by == [{"column": "b", "direction": "ASC"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_definition_order_by_api.py -v`
Expected: FAIL — `_row_to_out` has no `order_by` key; create/update don't set it.

- [ ] **Step 3: Implement**

In `app/api/v1/queries.py`:

`_row_to_out` — add `order_by`:

```python
        "params": row.params or {},
        "order_by": row.order_by or [],
        "created_at": row.created_at,
```

`create_definition` — set `order_by` on the new row (after building `params`):

```python
    order_by = [o.model_dump() for o in body.order_by] if body.order_by else []
    row = SqlDefinitionModel(
        name=body.name,
        db_alias=db,
        description=body.description,
        sql=body.sql.strip(),
        permission=permission,
        params=params,
        order_by=order_by,
    )
```

`update_definition` — apply `order_by` when provided (alongside the other `if body.x is not None` blocks):

```python
    if body.order_by is not None:
        row.order_by = [o.model_dump() for o in body.order_by]
```

YAML import (both `import_definitions_yaml` from the directory and `import_yaml_file` from upload) — when constructing `SqlDefinitionModel(...)`, add:

```python
        order_by=raw.get("order_by") or [],
```

YAML export (`export_definitions_yaml`) — add to the exported `entry` dict:

```python
            "order_by": row.order_by or [],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_definition_order_by_api.py -v`
Then full suite: `pytest -q` (ignore the 3 pre-existing `tests/test_sql_registry.py` failures — unrelated, registry loads from live Postgres).
Expected: new tests PASS; no new failures.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/queries.py tests/test_definition_order_by_api.py
git commit -m "feat(order-by): definition CRUD + YAML import/export carry order_by"
```

---

### Task 8: Admin UI — structured Order By editor

**Files:**
- Modify: `app/admin/js/app.js`
- Test: manual smoke (no JS test harness)

**Interfaces:**
- Consumes: `GET/POST/PUT /{db}/queries/definitions` now include `order_by` (Task 7).
- Produces: an "Order By" section in the definition create/edit form: add/remove rows, each a column-name input + ASC/DESC select; serialized to a JSON array on submit; initialized from the definition's `order_by` on edit.

> Vanilla JS, no automated tests. Read `app/admin/js/app.js` FIRST to find the real definition form builder + submit function and the real helpers (the earlier API-keys work used `openModal`/`esc`/`api`; confirm the actual names). Adapt to the real code.

- [ ] **Step 1: Locate the definition form**

Read `app/admin/js/app.js`; find where the SQL-definition create/edit form is rendered (search for `sdParams` / `Parameters (JSON)` / the definition save function) and where it POSTs/PUTs to `queries/definitions`.

- [ ] **Step 2: Add the Order By editor UI**

Below the Parameters field in the definition form, add an Order By section (adapt classes/helpers to the file's real ones):

```html
<label>Order By</label>
<div id="sdOrderByRows"></div>
<button type="button" class="btn btn-sm" onclick="addOrderByRow()">+ Add order column</button>
<div class="hint">Sorts on output column names. Applied by the Hub in the wrapper (works on MSSQL). Empty = no ordering.</div>
```

Add JS helpers (adapt names to the file):

```javascript
function addOrderByRow(column = '', direction = 'ASC') {
  const rows = document.getElementById('sdOrderByRows');
  const div = document.createElement('div');
  div.className = 'order-by-row';
  div.innerHTML = `
    <input class="ob-col" placeholder="COLUMN_NAME" value="${esc(column)}">
    <select class="ob-dir">
      <option ${direction === 'ASC' ? 'selected' : ''}>ASC</option>
      <option ${direction === 'DESC' ? 'selected' : ''}>DESC</option>
    </select>
    <button type="button" class="btn btn-xs" onclick="this.parentElement.remove()">✕</button>`;
  rows.appendChild(div);
}

function collectOrderBy() {
  return Array.from(document.querySelectorAll('#sdOrderByRows .order-by-row'))
    .map(r => ({ column: r.querySelector('.ob-col').value.trim(),
                 direction: r.querySelector('.ob-dir').value }))
    .filter(o => o.column);
}
```

- [ ] **Step 3: Wire into load + submit**

- When opening the edit form for an existing definition, after rendering, populate rows: `(def.order_by || []).forEach(o => addOrderByRow(o.column, o.direction));`.
- In the definition save function, include `order_by: collectOrderBy()` in the JSON body sent to POST/PUT.

- [ ] **Step 4: Manual smoke verification**

Start `uvicorn app.main:app --port 8000`, open `/admin`, log in as superuser, pick a database:
1. Create/edit a definition, add two Order By rows (e.g. `LastUpdateTime` DESC, `EmployeeID` ASC), save.
2. GET the definition (or reopen edit) → the two rows persist in order.
3. Run the query via Query Console with `fetch_mode: page` against an MSSQL alias → executes without the "ORDER BY illegal in subquery" error and rows come back sorted.
4. Remove all Order By rows, save → query still runs (unordered), no regression.

- [ ] **Step 5: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat(order-by): Admin UI structured Order By editor"
```

---

## Wrap-up verification

- [ ] Full suite `pytest -q` — only the 3 known pre-existing `test_sql_registry.py` failures remain; no new failures.
- [ ] `ruff check app/ tests/` and `ruff format app/ tests/` clean.
- [ ] `python -m scripts.init_db` runs the idempotent `order_by` ALTER without error.
- [ ] Manual smoke (Task 8 Step 4), ideally against an MSSQL alias to prove the derived-table ORDER BY problem is solved.

## Backward compatibility notes (from spec §7)

- Existing definitions default to `order_by = []` → every wrapper returns byte-identical SQL to today.
- The PostgreSQL platform table gets the new column via the idempotent `init_db` ALTER; fresh installs via `create_all` + column default.
- SQL definitions refresh via the existing `reload_from_db()` after CRUD — no restart needed for definition changes.
