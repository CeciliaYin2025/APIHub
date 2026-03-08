# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Oracle API Hub is a FastAPI-based REST gateway that exposes Oracle Database (11g+) resources as REST APIs. It uses DB-managed SQL definitions with Admin UI CRUD (YAML import supported for migration), oracledb for direct Oracle access, PostgreSQL + SQLAlchemy ORM for internal user/permission/SQL-definition management, and Celery+Redis for async task processing.

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
- **DB layer** (`app/db/`): Connection pool management
- **Tasks** (`app/tasks/`): Celery async task definitions
- **Models** (`app/models/`): SQLAlchemy ORM for User/Role/Permission and SQL Definition tables
- **Admin UI** (`app/admin/`): Vanilla JS SPA served as static files at `/admin`

### Dual Database Strategy

- **Oracle** (via oracledb pools, `app/db/multi_pool.py`): The data source/target. All dynamic SQL execution (queries, writes, procedures). `DatabaseRegistry` singleton manages one pool per configured database alias.
- **PostgreSQL** (via SQLAlchemy ORM, `app/db/session.py`): Platform-internal data — user/permission management (`api_hub_users`, `api_hub_roles`, `api_hub_permissions`) and SQL definitions (`api_hub_sql_definitions`).

### Multi-Database Routing

Database-scoped endpoints use `/{db}/` path prefix (e.g., `/{db}/queries/execute`). Database connections are configured via YAML files in `app/database_definitions/`. Legacy single-DB env vars (`ORACLE_DSN`, etc.) auto-create a "default" alias for backward compatibility.

### Query Engine — 5 Fetch Modes

The query service (`app/services/query_service.py`) supports: `page` (ROWNUM-based pagination, default), `all`, `batch` (cursor-style iteration), `top_n`, `one` (single row or 404).

### SQL Definition System

SQL definitions are stored in PostgreSQL (`api_hub_sql_definitions` table) as the primary source. At startup, `SqlRegistry` loads from DB; if the DB table is empty it falls back to YAML files in `app/sql_definitions/`. A one-time `POST /api/v1/queries/definitions/import-yaml` endpoint imports existing YAML definitions into the database. Full CRUD is available via API (`GET/POST/PUT/DELETE /api/v1/queries/definitions`) and the Admin UI. The in-memory registry is automatically refreshed after any CRUD operation.

### Async Task Processing

Celery with Redis broker. Two dedicated queues: `write` and `procedure`. Tasks retry up to 3 times with exponential backoff. `prefetch_multiplier=1` to protect DB connections.

### Temporary Files

測試資料、API 回應暫存檔等放在 `temp/` 目錄下，該目錄已加入 `.gitignore`，不會被版控追蹤。

## Code Conventions

- **Python**: 3.10+, line length 120, linted with ruff
- **Parameterized queries only**: All SQL uses bind variables (`:param_name`), never string interpolation
- **Table name validation**: Strict regex in `write_service.py` to prevent injection
- **Auth**: JWT (HS256) with RBAC. Permission model is `resource:action` with wildcard support
- **Config**: All settings via environment variables through Pydantic Settings (`app/core/config.py`)
