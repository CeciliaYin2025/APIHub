# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- **MCP server** (`mcp_server/`): standalone MCP process exposing SQL definitions as
  dynamic tools. Thin HTTP proxy over the REST API (never imports `app/` or touches
  DBs directly). stdio (`python -m mcp_server`) + Streamable HTTP
  (`uvicorn mcp_server.http_app:app`). Settings: `MCP_*` env vars / `.env.mcp`.

### Data Source Databases vs Platform Store

Two distinct database roles, do not confuse them:

- **Data source databases** (Oracle / PostgreSQL / MSSQL, configured via `app/database_definitions/*.yaml`): the targets the API exposes. All dynamic SQL execution (queries, writes, procedures). `DatabaseRegistry` manages one pool per alias; each alias has a `db_type` that selects an adapter from `app/db/adapters/` and a dialect from `app/db/dialects/`. Per-alias password overrides via `DB_{ALIAS_UPPER}_PASSWORD` env vars.
- **Platform store** (internal PostgreSQL, configured via `POSTGRES_*` env vars, accessed via SQLAlchemy ORM in `app/db/session.py`): user/permission management (`api_hub_users`, `api_hub_roles`, `api_hub_permissions`) and SQL definitions (`api_hub_sql_definitions`). Always PostgreSQL — not pluggable.

### Multi-Database Routing

Database-scoped endpoints use `/{db}/` path prefix (e.g., `/{db}/queries/execute`), where `{db}` is the YAML `alias`. Legacy single-DB env vars (`ORACLE_DSN`, etc.) auto-create a `default` alias for backward compatibility when no YAML is present.

### Configuring Data Sources (`database_definitions`)

All data-source connections (read + write/procedure targets) are defined in `app/database_definitions/databases.yaml`. The file is a **YAML list** where each entry is one alias; `DatabaseRegistry.load()` (in `app/db/multi_pool.py`) reads it. There is no separate "source" vs "target" concept — an alias is a connection that serves both queries (read) and writes/procedures (write); add multiple aliases for multiple sources/targets.

Per-entry fields (loaded into `DatabaseConfig`):

- `alias` (required): identifier and API path segment. Regex `^[a-zA-Z][a-zA-Z0-9_]{0,63}$`.
- `db_type`: `oracle` (default), `postgresql`, or `mssql` — selects the adapter (`app/db/adapters/`) and dialect (`app/db/dialects/`).
- `description`: shown in `GET /api/v1/databases` and the Admin UI DB selector.
- Oracle: `dsn` (`host:port/service`).
- PostgreSQL / MSSQL: `host`, `port`, `database`.
- Common: `user`, `password`, `pool_min`, `pool_max`, `pool_increment` (Oracle only).

Conventions to follow:

- **No plaintext passwords in the YAML.** Leave `password: ""` and supply it via the env var `DB_{ALIAS_UPPER}_PASSWORD` (e.g. alias `pg_test` → `DB_PG_TEST_PASSWORD`). The env var, when set, overrides the YAML value (`multi_pool.py` `_load_file`). Every alias needs its own var or its connection fails.
- **Definitions load once at startup** (`db_registry.load()` in the lifespan); pools are created lazily on first use. There is **no hot-reload endpoint** for data sources (unlike SQL definitions) — editing `databases.yaml` requires a **process/container restart** to take effect, and the Celery workers must be restarted too (they connect via the same YAML).
- **Both compose stacks bind-mount the directory** so edits need only a restart, not a rebuild: root `docker-compose.yaml` (local dev) and `deploy/apihub/docker-compose.yml` (ISAP gateway; `deploy/apihub-test/` for the test/UAT stack) mount `app/database_definitions` into the `api` and both celery services. When adding an alias there, also add its `DB_*_PASSWORD` to the corresponding `.env`.

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
