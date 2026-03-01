# Oracle API Hub

A generic RESTful API platform built with **FastAPI** that exposes Oracle Database (11g+) tables, views, functions, and procedures as REST APIs. Designed as a universal data gateway for transforming Oracle data into open data format consumable by any system.

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────────┐
│   Clients   │────▶│              FastAPI Application              │
│ (Web/Mobile/│     │  ┌──────────┐ ┌────────┐ ┌───────────────┐  │
│  Services)  │     │  │   Auth   │ │  Rate  │ │  Permission   │  │
└─────────────┘     │  │  (JWT)   │ │ Limiter│ │  Checker      │  │
                    │  └──────────┘ └────────┘ └───────────────┘  │
                    │                                              │
                    │  ┌───────────────────────────────────────┐   │
                    │  │           API Endpoints                │   │
                    │  │  /queries   /procedures   /writes     │   │
                    │  └────────────┬──────────────┬───────────┘   │
                    └───────────────┼──────────────┼───────────────┘
                                    │              │
                    ┌───────────────▼──┐   ┌──────▼────────────┐
                    │  cx_Oracle Pool   │   │  Celery + Redis   │
                    │  (Sync Queries)   │   │  (Async Writes)   │
                    └───────────────┬──┘   └──────┬────────────┘
                                    │              │
                              ┌─────▼──────────────▼─────┐
                              │     Oracle Database       │
                              │     (11g / 11.1.0.4)      │
                              └──────────────────────────┘
```

## Features

### 1. SQL Query as Open Data (`/api/v1/queries`)
- Define SQL queries in YAML files — no code changes needed
- Auto-pagination using Oracle 11g compatible `ROWNUM`
- Returns structured JSON with columns, data, and pagination metadata
- Hot-reload SQL definitions without restarting

### 2. Oracle Function & Procedure Execution (`/api/v1/procedures`)
- Call stored procedures with IN/OUT parameters
- Call functions and capture return values
- Sync and async (Celery) execution modes

### 3. Data Write-Back (`/api/v1/writes`)
- INSERT / UPDATE / DELETE with parameterized SQL (injection-safe)
- **Async mode**: enqueue write operations via Celery + Redis to buffer DB load
- Batch insert via `executemany` for high throughput

### 4. Task Queue (Celery + Redis)
- Separate queues for `write` and `procedure` operations
- Automatic retry with exponential backoff
- Worker concurrency tuned to protect DB connections
- Flower dashboard for monitoring at port 5555

### 5. Authentication & RBAC
- JWT-based authentication (`/api/v1/auth/token`)
- Role-Based Access Control with wildcard support
- Permission model: `resource:action` (e.g., `query:employees` + `read`)
- Superuser bypass for administrative access

### 6. Rate Limiting
- Per-client sliding window rate limiter (configurable)

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your Oracle DSN, credentials, etc.

# 2. Start services
docker-compose up -d

# 3. Initialize database tables and admin user
docker-compose exec api python -m scripts.init_db

# 4. Get a token
curl -X POST http://localhost:8000/api/v1/auth/token \
  -d "username=admin&password=changeme"

# 5. Query data
curl -X POST http://localhost:8000/api/v1/queries/execute \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query_name": "employees", "params": {"dept": "IT"}}'
```

## Adding a New Query Definition

Create a YAML file in `app/sql_definitions/`:

```yaml
name: my_report
description: "Monthly sales report"
sql: |
  SELECT region, product, SUM(amount) total_amount
  FROM sales
  WHERE sale_date BETWEEN :start_date AND :end_date
  GROUP BY region, product
  ORDER BY total_amount DESC
params:
  start_date:
    type: date
    required: true
    description: "Start date (YYYY-MM-DD)"
  end_date:
    type: date
    required: true
    description: "End date (YYYY-MM-DD)"
permission: "query:my_report"
```

Then call `POST /api/v1/queries/definitions/reload` to pick it up without restart.

## Configuration

All settings via environment variables (see `.env.example`):

| Variable | Description | Default |
|---|---|---|
| `ORACLE_DSN` | Oracle connection string | `localhost:1521/orcl` |
| `ORACLE_POOL_MIN` / `MAX` | Connection pool size | `2` / `10` |
| `SECRET_KEY` | JWT signing key | (change this!) |
| `CELERY_BROKER_URL` | Redis URL for Celery | `redis://localhost:6379/1` |
| `RATE_LIMIT_PER_MINUTE` | Max requests per client per minute | `60` |

## Project Structure

```
app/
├── main.py                  # FastAPI app entry point
├── core/
│   ├── config.py            # Settings from env vars
│   └── security.py          # JWT + password hashing
├── db/
│   ├── oracle_pool.py       # cx_Oracle connection pool
│   └── session.py           # SQLAlchemy session (for ORM)
├── models/
│   └── user.py              # User, Role, Permission ORM models
├── schemas/
│   ├── auth.py              # Auth request/response schemas
│   └── query.py             # Query/Procedure/Write schemas
├── services/
│   ├── auth_service.py      # Auth logic + PermissionChecker
│   ├── sql_registry.py      # YAML SQL definition loader
│   ├── query_service.py     # Query execution with pagination
│   ├── procedure_service.py # Oracle proc/func execution
│   └── write_service.py     # DML operations
├── tasks/
│   ├── celery_app.py        # Celery configuration
│   ├── write_tasks.py       # Async write tasks
│   └── procedure_tasks.py   # Async procedure tasks
├── middleware/
│   └── rate_limiter.py      # Sliding window rate limiter
├── api/v1/
│   ├── auth.py              # Auth endpoints
│   ├── queries.py           # Query endpoints
│   ├── procedures.py        # Procedure endpoints
│   ├── writes.py            # Write endpoints
│   └── health.py            # Health check
└── sql_definitions/         # YAML query definitions
    ├── employees.yaml
    ├── departments.yaml
    └── inventory_view.yaml
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v
```
