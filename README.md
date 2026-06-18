# Oracle API Hub

A generic RESTful API platform built with **FastAPI** that exposes **Oracle**, **PostgreSQL**, and **Microsoft SQL Server** tables, views, functions, and procedures as REST APIs. SQL definitions are managed in PostgreSQL with full CRUD via Admin UI (YAML import supported for migration). Designed as a universal data gateway for transforming relational data into open data format consumable by any system.

> The "Oracle" in the project name reflects its origin; the platform now connects to **multiple database engines** through a unified adapter layer. A single API Hub instance can serve REST endpoints backed by any mix of Oracle / PostgreSQL / MSSQL databases.

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
                    │  │ /{db}/queries  /procedures  /writes   │   │
                    │  └────────────┬──────────────┬───────────┘   │
                    └───────────────┼──────────────┼───────────────┘
                                    │              │
                    ┌───────────────▼──────┐   ┌──▼────────────────┐
                    │  DatabaseRegistry    │   │  Celery + Redis   │
                    │  (multi-engine pool) │   │  (Async Writes)   │
                    └─┬──────────┬─────────┘   └──┬────────────────┘
                      │          │                │
        ┌─────────────┘   ┌──────┴──────┐   ┌─────┴──────────────┐
        ▼                 ▼             ▼   ▼                    │
   ┌─────────┐      ┌────────────┐  ┌────────┐    ┌─────────────▼─────┐
   │ Oracle  │      │ PostgreSQL │  │  MSSQL │    │   PostgreSQL       │
   │ (11g+)  │      │ (data src) │  │        │    │ (Users, SQL Defs)  │
   └─────────┘      └────────────┘  └────────┘    └────────────────────┘
       \________________ data sources ___________/        platform store
```

## Supported Database Engines

The platform serves REST endpoints backed by three engines through a unified `PoolAdapter` layer (`app/db/adapters/`). All three drivers ship as declared dependencies — no extra install needed.

| Engine | Driver | Adapter | YAML `db_type` |
|---|---|---|---|
| Oracle (11g+) | `oracledb` | `OraclePoolAdapter` | `oracle` |
| PostgreSQL | `psycopg2-binary` | `PostgresPoolAdapter` | `postgresql` |
| Microsoft SQL Server | `pymssql` | `MssqlPoolAdapter` | `mssql` |

### Configuring multiple databases

Drop YAML files into `app/database_definitions/` — each file is a list of database entries. A single API Hub instance can serve any mix of engines side-by-side; each is reachable via its `alias` in the URL path: `/api/v1/{alias}/queries/execute`.

```yaml
# app/database_definitions/databases.yaml
- alias: default
  db_type: oracle
  description: "Oracle UAT database"
  dsn: "172.31.3.85:1531/UAT"
  user: "apps"
  password: "apps"           # or omit and set DB_DEFAULT_PASSWORD env var
  pool_min: 2
  pool_max: 10

- alias: pg_reporting
  db_type: postgresql
  description: "PostgreSQL reporting warehouse"
  host: "pg.internal"
  port: 5432
  database: "reporting"
  user: "ro_user"
  password: "***"
  pool_min: 2
  pool_max: 10

- alias: crm
  db_type: mssql
  description: "Legacy CRM on SQL Server"
  host: "mssql.internal"
  port: 1433
  database: "CRM"
  user: "api_user"
  password: "***"
  pool_min: 2
  pool_max: 5
```

**Password override via environment variable:** any entry's `password` can be overridden by `DB_{ALIAS_UPPERCASE}_PASSWORD` (e.g. `DB_DEFAULT_PASSWORD`, `DB_CRM_PASSWORD`) — preferred for production so secrets stay out of YAML.

> **Note on the platform store:** the internal PostgreSQL database that holds users, roles, permissions, and SQL definitions is separate from the data-source PostgreSQL configured above — it is configured via the `POSTGRES_*` environment variables (see [Configuration](#configuration)).

## Features

### 1. SQL Query as Open Data (`/api/v1/{db}/queries`)
- Works against any configured Oracle / PostgreSQL / MSSQL alias
- SQL definitions stored in PostgreSQL with full CRUD API and Admin UI management
- **YAML export/import** for cross-database migration: export definitions from one database, upload into another
- YAML import endpoint for migrating existing server-side definitions (`POST /queries/definitions/import-yaml`)
- Auto-pagination (Oracle 11g compatible `ROWNUM`; standard `LIMIT/OFFSET` for PostgreSQL/MSSQL)
- Returns structured JSON with columns, data, and pagination metadata
- In-memory registry auto-refreshes after any CRUD operation
- Trailing semicolons in a stored definition's SQL are stripped automatically before execution — every fetch mode wraps the SQL as a subquery (`… FROM (<sql>) sub_`), so a trailing `;` would otherwise break every engine (e.g. MSSQL `Incorrect syntax near ';'`)

### 2. Function & Stored Procedure Execution (`/api/v1/{db}/procedures`)
- Call stored procedures with IN/OUT parameters (Oracle PL/SQL, PostgreSQL functions, MSSQL T-SQL procedures)
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
- Role-Based Access Control with three matching modes: exact (`query:employees`), **prefix wildcard** (`query:HR*` matches `query:HR_ORG`, `query:HR_Emps`, …), and full-category (`query:*`)
- Permission model: `resource:action` (e.g., `query:employees` + `read`); the action must always match exactly
- Superuser bypass for administrative access

### 6. Rate Limiting
- Per-client sliding window rate limiter (configurable)

## System-to-System Integration

Oracle API Hub 設計為給其他系統（ERP、HR、BI 等）透過 REST API 存取 Oracle 資料的中繼平台。以下說明外部系統如何整合，以及權限設置方式。

### 整合架構

```
ERP System ──┐
             ├─► POST /auth/token (取得 JWT)
             ├─► GET  /{db}/queries/execute (帶 Bearer token)
             │
HR System  ──┤   每個系統各自的帳號 + 角色 + 權限
             ├─► POST /{db}/writes/execute
             │
BI System  ──┘
             └─► POST /{db}/queries/execute (唯讀)
```

### Step 1: 建立專用服務帳號

用 superuser (admin) 幫每個外部系統建立專屬帳號：

```bash
# 取得 admin token
curl -X POST http://your-host:8000/api/v1/auth/token \
  -d "username=admin&password=admin_password"

# 建立服務帳號
curl -X POST http://your-host:8000/api/v1/auth/users \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "erp_system",
    "password": "strong-random-password",
    "full_name": "ERP System Service Account",
    "is_superuser": false,
    "role_ids": [1]
  }'
```

### Step 2: 設定權限 (最小權限原則)

權限模型是 `resource:action`。`resource` 結尾的 `*` 視為**前綴比對**（`*` 之前的字串即為前綴），`action` 一律須完全相符：

```bash
# 建立精細權限 — 只允許查詢特定資源
curl -X POST http://your-host:8000/api/v1/auth/permissions \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"resource": "query:employees", "action": "read", "description": "查詢員工資料"}'

# 前綴萬用字元 — 一次涵蓋同前綴的多個 query（如 query:HR_ORG、query:HR_Emps）
curl -X POST http://your-host:8000/api/v1/auth/permissions \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"resource": "query:HR*", "action": "read", "description": "讀取所有 HR 開頭查詢"}'

# 整類萬用字元 — 允許讀取所有 query
curl -X POST http://your-host:8000/api/v1/auth/permissions \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"resource": "query:*", "action": "read", "description": "讀取所有查詢"}'
```

**權限粒度範例：**

| resource | action | 說明 |
|---|---|---|
| `query:employees` | `read` | 只能查員工（精確比對） |
| `query:HR*` | `read` | 能查所有 `query:HR` 開頭的 query（前綴比對，如 `HR_ORG`、`HR_Emps`） |
| `query:*` | `read` | 能查所有已定義的 query（整類） |
| `procedure:get_salary` | `execute` | 只能執行薪資 SP |
| `write:orders` | `write` | 只能寫入訂單表 |

> **前綴萬用字元語意（1.6.0 起）：** 結尾 `*` 一律按「前綴」比對，因此 `query:*`（前綴 `query:`）與 `query:HR*`（前綴 `query:HR`）走同一條規則。舊版僅支援結尾 `:*` 的整類萬用字元，`query:HR*` 這類部分前綴在舊版不會匹配（一律 403）。

### Step 3: 建立角色並綁定權限

```bash
curl -X POST http://your-host:8000/api/v1/auth/roles \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "erp_readonly",
    "description": "ERP系統唯讀角色",
    "permission_ids": [1, 2, 3]
  }'
```

### Step 4: 外部系統呼叫方式

```python
import requests

API_BASE = "http://your-host:8000/api/v1"

# Step 1: 取得 token
resp = requests.post(f"{API_BASE}/auth/token", data={
    "username": "erp_system",
    "password": "strong-random-password"
})
token = resp.json()["access_token"]

# Step 2: 帶 token 呼叫 API
headers = {"Authorization": f"Bearer {token}"}

# 執行查詢
result = requests.post(f"{API_BASE}/default/queries/execute",
    headers=headers,
    json={"query_name": "employees", "params": {"dept_id": 10}}
)
print(result.json())
```

### Token 管理建議

| 項目 | 建議 |
|---|---|
| Token 有效期 | 預設 60 分鐘，內網環境可調整 `ACCESS_TOKEN_EXPIRE_MINUTES`（如 `1440` = 24 小時） |
| 自動刷新 | 外部系統應在 token 過期前或收到 `401` 時自動重新呼叫 `/auth/token` |
| 帳號隔離 | 每個系統獨立帳號，方便追蹤與個別撤銷權限 |

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

### Deploying to the ISAP platform gateway

To run behind the shared ISAP nginx gateway at `/apihub/`, use the
self-registering stack in [`deploy/isap/`](deploy/isap/README.md). It creates
its DB in `isap-postgres`, uses `isap-redis` for Celery, and drops its own
nginx route — no edits to the `isap-infrastructure` repo. Set `ROOT_PATH=/apihub`
so the app and admin UI generate prefix-aware URLs.

## Adding a New Query Definition

### Via Admin UI (Recommended)

1. Open `http://localhost:8000/admin` and log in as a superuser
2. Navigate to **SQL Definitions** page
3. Click **+ New Definition**
4. Fill in name, description, SQL, permission, and parameters (JSON)
5. Click **Create** — the definition is immediately available for queries

### Via API

```bash
curl -X POST http://localhost:8000/api/v1/queries/definitions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_report",
    "description": "Monthly sales report",
    "sql": "SELECT region, product, SUM(amount) total_amount\nFROM sales\nWHERE sale_date BETWEEN :start_date AND :end_date\nGROUP BY region, product\nORDER BY total_amount DESC",
    "permission": "query:my_report",
    "params": {
      "start_date": {"type": "date", "required": true, "description": "Start date (YYYY-MM-DD)"},
      "end_date": {"type": "date", "required": true, "description": "End date (YYYY-MM-DD)"}
    }
  }'
```

### Migrating from YAML

If you have existing YAML definitions in `app/sql_definitions/`, import them into the database:

```bash
curl -X POST http://localhost:8000/api/v1/queries/definitions/import-yaml \
  -H "Authorization: Bearer <token>"
```

Or use the **Import YAML** button on the Admin UI SQL Definitions page.

### Exporting & Importing Definitions Between Databases

Export all definitions from a database as a YAML file:

```bash
curl -o definitions.yaml \
  http://localhost:8000/api/v1/default/queries/definitions/export-yaml \
  -H "Authorization: Bearer <token>"
```

Upload that file into a different database:

```bash
curl -X POST http://localhost:8000/api/v1/staging/queries/definitions/import-yaml-file \
  -H "Authorization: Bearer <token>" \
  -F "file=@definitions.yaml"
```

Or use the **Export YAML** / **Upload YAML** buttons on the Admin UI SQL Definitions page.

## Configuration

**Data sources** (Oracle / PostgreSQL / MSSQL the API exposes): configured via YAML files in `app/database_definitions/` — see [Supported Database Engines](#supported-database-engines). Passwords can be supplied per-alias through `DB_{ALIAS}_PASSWORD` environment variables.

**Platform settings** via environment variables (see `.env.example`):

| Variable | Description | Default |
|---|---|---|
| `ORACLE_DSN` | Legacy single-Oracle DSN — used to auto-create a `default` alias if no YAML is present | `localhost:1521/orcl` |
| `ORACLE_POOL_MIN` / `MAX` | Legacy single-Oracle pool size | `2` / `10` |
| `DB_{ALIAS}_PASSWORD` | Override password for the YAML database with `alias: {alias}` (e.g. `DB_DEFAULT_PASSWORD`) | — |
| `POSTGRES_HOST` | Platform-internal PostgreSQL host (users, SQL defs) | `localhost` |
| `POSTGRES_PORT` | Platform PostgreSQL port | `5432` |
| `POSTGRES_DB` | Platform PostgreSQL database name | `api_hub` |
| `POSTGRES_USER` | Platform PostgreSQL user | `api_hub` |
| `POSTGRES_PASSWORD` | Platform PostgreSQL password | `changeme` |
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
│   ├── multi_pool.py        # DatabaseRegistry — multi-engine pool manager
│   ├── pool_factory.py      # Builds the right adapter per db_type
│   ├── pool_adapter.py      # PoolAdapter abstract base
│   ├── adapters/
│   │   ├── oracle_adapter.py     # oracledb session pool
│   │   ├── postgresql_adapter.py # psycopg2 ThreadedConnectionPool
│   │   └── mssql_adapter.py      # pymssql queue-based pool
│   ├── types.py             # DbType = "oracle" | "postgresql" | "mssql"
│   └── session.py           # SQLAlchemy session (for ORM)
├── database_definitions/    # YAML database connection configs (multi-DB)
├── models/
│   ├── user.py              # User, Role, Permission ORM models
│   └── sql_definition.py    # SQL Definition ORM model (PostgreSQL)
├── schemas/
│   ├── auth.py              # Auth request/response schemas
│   ├── query.py             # Query/Procedure/Write schemas
│   └── sql_definition.py    # SQL Definition CRUD schemas
├── services/
│   ├── auth_service.py      # Auth logic + PermissionChecker
│   ├── sql_registry.py      # SQL definition registry (DB primary, YAML fallback)
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
│   ├── queries.py           # Query endpoints (CRUD + execute)
│   ├── procedures.py        # Procedure endpoints
│   ├── writes.py            # Write endpoints
│   └── health.py            # Health check
└── sql_definitions/         # YAML query definitions (fallback/import source)
    ├── employees.yaml
    ├── departments.yaml
    └── inventory_view.yaml

examples/
└── read_rd_tc_all.py        # Client example: auth + execute a query as a scoped reader account
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v
```
