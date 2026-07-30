# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Oracle API Hub is a FastAPI-based REST gateway that exposes **Oracle (11g+), PostgreSQL, and Microsoft SQL Server** resources as REST APIs through a unified adapter + dialect layer. The "Oracle" in the name is historical — a single API Hub instance can serve endpoints backed by any mix of the three engines. It uses DB-managed SQL definitions with Admin UI CRUD (YAML import supported for migration), engine-specific drivers (`oracledb`, `psycopg2-binary`, `pymssql`) for direct data-source access, an internal PostgreSQL + SQLAlchemy ORM store for user/permission/SQL-definition management, and Celery+Redis for async task processing.

## Commands

```bash
# Install (dev mode)
pip install -e ".[dev]"

# Run the app
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Run all tests
pytest -v

# Run a single test file
pytest tests/test_security.py -v

# Run a single test
pytest tests/test_security.py::test_password_hashing -v

# Lint
ruff check app/ tests/

# Format
ruff format app/ tests/

# Initialize DB tables and default admin user
python -m scripts.init_db

# Trim the audit trail (nothing does this automatically; schedule it)
python -m scripts.purge_audit_logs [--days N] [--dry-run]

# Docker
docker-compose up -d
docker-compose exec api python -m scripts.init_db

# Celery workers
celery -A app.tasks.celery_app worker -Q write -c 4
celery -A app.tasks.celery_app worker -Q procedure -c 2
celery -A app.tasks.celery_app flower --port=5555
```

## Architecture

**Layered design**: Middleware → Router → Service → Data Access

- **Entry point**: `app/main.py` — FastAPI app with lifespan (init pools, load SQL registry)
- **Routers** (`app/api/v1/`): auth, queries, procedures, writes, schemas, databases, health
- **Services** (`app/services/`): Business logic layer called by routers
- **DB layer** (`app/db/`): Multi-engine connection pool management
  - `multi_pool.py` — `DatabaseRegistry` singleton, one `PoolAdapter` per alias
  - `pool_factory.py` + `adapters/` — per-engine pool implementations (oracle/postgresql/mssql)
  - `dialects/` — per-engine SQL rewriting (pagination, bind conversion, procedure calls, introspection)
  - `session.py` — SQLAlchemy session for the platform store
- **Tasks** (`app/tasks/`): Celery async task definitions
- **Models** (`app/models/`): SQLAlchemy ORM for User/Role/Permission and SQL Definition tables
- **Admin UI** (`app/admin/`): Vanilla JS SPA served as static files at `/admin`

### Data Source Databases vs Platform Store

Two distinct database roles, do not confuse them:

- **Data source databases** (Oracle / PostgreSQL / MSSQL, configured via `app/database_definitions/*.yaml`): the targets the API exposes. All dynamic SQL execution (queries, writes, procedures). `DatabaseRegistry` manages one pool per alias; each alias has a `db_type` that selects an adapter from `app/db/adapters/` and a dialect from `app/db/dialects/`. Per-alias password overrides via `DB_{ALIAS_UPPER}_PASSWORD` env vars.
- **Platform store** (internal PostgreSQL, configured via `POSTGRES_*` env vars, accessed via SQLAlchemy ORM in `app/db/session.py`): user/permission management (`api_hub_users`, `api_hub_roles`, `api_hub_permissions`) and SQL definitions (`api_hub_sql_definitions`). Always PostgreSQL — not pluggable.

### Multi-Database Routing

Database-scoped endpoints use `/{db}/` path prefix (e.g., `/{db}/queries/execute`), where `{db}` is the YAML `alias`. Legacy single-DB env vars (`ORACLE_DSN`, etc.) auto-create a `default` alias for backward compatibility when no YAML is present.

### Dialect Layer

`app/db/dialects/{oracle,postgresql,mssql}_dialect.py` isolate engine differences so the same SQL definition can target any engine:
- **Pagination**: Oracle wraps with `ROWNUM`; PostgreSQL/MSSQL use `LIMIT/OFFSET` (or `OFFSET ... FETCH NEXT` for MSSQL)
- **Bind conversion**: SQL definitions are written with Oracle-style `:name` binds; PG/MSSQL dialects rewrite to `%(name)s` for psycopg2/pymssql at execution time
- **Procedure/function calls**: PL/SQL `BEGIN ... END;` vs PG `CALL`/`SELECT func()` vs MSSQL `EXEC`
- **Schema introspection**: `ALL_TAB_COLUMNS` vs `information_schema.columns` vs `sys.columns`

When adding engine-specific behavior, extend the `Dialect` base class — never branch on `db_type` inside services.

### Query Engine — 5 Fetch Modes

The query service (`app/services/query_service.py`) supports: `page` (dialect-aware pagination, default), `all`, `batch` (cursor-style iteration), `top_n`, `one` (single row or 404).

### SQL Definition System

SQL definitions are stored in PostgreSQL (`api_hub_sql_definitions` table) as the primary source. At startup, `SqlRegistry` loads from DB; if the DB table is empty it falls back to YAML files in `app/sql_definitions/`. A one-time `POST /api/v1/queries/definitions/import-yaml` endpoint imports existing YAML definitions into the database. Full CRUD is available via API (`GET/POST/PUT/DELETE /api/v1/queries/definitions`) and the Admin UI. The in-memory registry is automatically refreshed after any CRUD operation.

### Audit Trail & API Usage Report

`AuditMiddleware` writes one `api_hub_audit_logs` row per request, best-effort (failures swallowed, never affecting the response). It runs *after* `call_next` and so cannot read the consumed request body — routers hand over the details by calling `app/middleware/audit.py::record_audit(request, action_type, action, params, result_rows)`, which stashes them on `request.state`. A new endpoint worth reporting on must call `record_audit` itself; nothing picks it up automatically.

`/api/v1/audit/*` (`app/api/v1/audit.py` + `app/services/audit_report_service.py`) reports on that trail: `usage` aggregates by any combination of user / db / api / day, `logs` lists the individual requests, `logs/{id}` returns one with full params, `filters` feeds the UI dropdowns. **Superuser only** — the report exposes every caller's request parameters. Statistics are a live `GROUP BY`, not a pre-aggregated table.

- **`params` is stored unmasked and untruncated** — a deliberate decision; do not add masking or truncation on the write path. Nothing bounds table growth except `AUDIT_RETENTION_DAYS` plus `python -m scripts.purge_audit_logs` (manual/scheduled, not a Celery beat job). `AUDIT_PARAMS_INLINE_LIMIT` only caps API response size, never what is stored.
- **`action_type`** (`query` / `procedure` / `write` / `definition` / `report`) separates data-access volume from admin operations; `usage` and `logs` default to `query,procedure,write`.

Aggregation depends on PostgreSQL-only constructs (`JSONB`, `COUNT(*) FILTER`, `date()`) and `pytest` has no live Postgres, so `tests/test_audit_report_service.py` covers parsing/validation and compiled-SQL shape while the **numeric** correctness lives in `tests/e2e/usage-report.spec.js`.

### Async Task Processing

Celery with Redis broker. Two dedicated queues: `write` and `procedure`. Tasks retry up to 3 times with exponential backoff. `prefetch_multiplier=1` to protect DB connections.

### Temporary Files

測試資料、API 回應暫存檔等放在 `temp/` 目錄下，該目錄已加入 `.gitignore`，不會被版控追蹤。

## Code Conventions

- **Python**: 3.10+, line length 120, linted with ruff
- **Parameterized queries only**: All SQL definitions use Oracle-style bind variables (`:param_name`), never string interpolation. The dialect layer rewrites binds to `%(param_name)s` for PostgreSQL/MSSQL automatically — definitions stay engine-agnostic.
- **Table name validation**: Strict regex in `write_service.py` (per-dialect) to prevent injection
- **Auth**: JWT (HS256) with RBAC. Permission model is `resource:action` with wildcard support
- **Config**: All settings via environment variables through Pydantic Settings (`app/core/config.py`)
