# Oracle API Hub

A generic RESTful API platform built with **FastAPI** that exposes Oracle Database (11g+) tables, views, functions, and procedures as REST APIs. SQL definitions are managed in PostgreSQL with full CRUD via Admin UI (YAML import supported for migration). Designed as a universal data gateway for transforming Oracle data into open data format consumable by any system.

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
                    ┌───────────────▼──┐   ┌──────▼────────────┐
                    │  Oracle Database  │   │  PostgreSQL        │
                    │  (11g / 11.1.0.4) │   │  (Users, SQL Defs) │
                    └──────────────────┘   └───────────────────┘
```

## Features

### 1. SQL Query as Open Data (`/api/v1/queries`)
- SQL definitions stored in PostgreSQL with full CRUD API and Admin UI management
- **YAML export/import** for cross-database migration: export definitions from one database, upload into another
- YAML import endpoint for migrating existing server-side definitions (`POST /queries/definitions/import-yaml`)
- Auto-pagination using Oracle 11g compatible `ROWNUM`
- Returns structured JSON with columns, data, and pagination metadata
- In-memory registry auto-refreshes after any CRUD operation

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

權限模型是 `resource:action`，支援萬用字元（`*`）：

```bash
# 建立精細權限 — 只允許查詢特定資源
curl -X POST http://your-host:8000/api/v1/auth/permissions \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"resource": "query:employees", "action": "read", "description": "查詢員工資料"}'

# 萬用字元 — 允許讀取所有 query
curl -X POST http://your-host:8000/api/v1/auth/permissions \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"resource": "query:*", "action": "read", "description": "讀取所有查詢"}'
```

**權限粒度範例：**

| resource | action | 說明 |
|---|---|---|
| `query:employees` | `read` | 只能查員工 |
| `query:*` | `read` | 能查所有已定義的 query |
| `procedure:get_salary` | `execute` | 只能執行薪資 SP |
| `write:orders` | `write` | 只能寫入訂單表 |

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

All settings via environment variables (see `.env.example`):

| Variable | Description | Default |
|---|---|---|
| `ORACLE_DSN` | Oracle connection string | `localhost:1521/orcl` |
| `ORACLE_POOL_MIN` / `MAX` | Connection pool size | `2` / `10` |
| `POSTGRES_HOST` | PostgreSQL host | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_DB` | PostgreSQL database name | `api_hub` |
| `POSTGRES_USER` | PostgreSQL user | `api_hub` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `changeme` |
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
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v
```
