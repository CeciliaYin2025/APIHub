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

### 6. ISAP SSO (optional, `ADMIN_SSO_ENABLED`)

Sign in to the Admin UI with an ISAP account instead of a local password, via isap-auth's app-code flow.

- `GET /api/v1/auth/sso/login` redirects to isap-auth's `/authorize` with a random `state`, stored in an `httpOnly` `SameSite=Lax` cookie
- `GET /api/v1/auth/sso/callback` verifies that `state`, exchanges the one-time code for user info over the internal network, maps it to a local user and hands the minted JWT to the SPA in a **URL fragment** (never a query parameter, which would land in access logs)
- The local password form stays on the login page, so a broken isap-auth never locks administrators out
- `SSO_AUTOCREATE` decides whether an unknown ISAP user is auto-provisioned as a role-less local account or rejected; it governs the MCP identity bridge too, and `MCP_BRIDGE_AUTOCREATE` remains as an alias
- Both endpoints return 404 while `ADMIN_SSO_ENABLED` is off, and the UI hides the button
- `python -m scripts.register_isap_app` registers this instance's `redirect_uris` with isap-auth. It runs automatically as a compose one-shot (`apihub-app-register`), is idempotent, and exits 0 without calling out when no bootstrap token is configured

### 7. OAuth 2.1 Resource Server (optional, `OAUTH_RS_ENABLED`)

Call the REST API with an isap-auth access token — no local password, no API key. This is the API-side counterpart to §6: that one is the browser login, this one lets any OAuth client (a script, another app, an MCP host) authenticate as its own ISAP user.

- A bearer token that is neither an API key (`ahk_`) nor a valid local JWT is validated against isap-auth by **RFC 7662 introspection** — its access tokens are opaque, so there is no signature to verify locally
- Verified claims map to a local user through the same `resolve_sso_user()` the Admin UI and the MCP bridge use, so `SSO_AUTOCREATE` governs provisioning identically
- **Only the identity crosses over.** Authorization stays entirely local: an `is_superuser` claim from isap-auth grants nothing here
- Central revocation applies — an isap-auth token dies with the SSO session or a disabled account, bounded by `OAUTH_INTROSPECTION_CACHE_SECONDS` (default 60s, which also caps the per-token introspection rate)
- `GET /.well-known/oauth-protected-resource` publishes RFC 9728 metadata and 401s carry a matching `WWW-Authenticate`, so an OAuth client can discover the authorization server. Behind the gateway this needs the root-level rewrite in `deploy/*/gateway-conf/oauth-wellknown-*.snippet.conf`
- isap-auth has no `client_credentials` grant, so **unattended callers still use API keys** — OAuth adds a user identity, it does not replace service accounts
- Off by default: without `OAUTH_RS_ENABLED` plus `OAUTH_INTROSPECTION_URL` the branch is inert and the metadata route 404s

### 8. Bulk Extraction (`scripts/extract_query.py`)

Stream any registered definition's full result set to a local file — the bulk counterpart to the MCP tools, which return rows into an LLM's context and so do not suit six-figure row counts.

```bash
export APIHUB_URL=https://<host>/apihub-test
export APIHUB_API_KEY=ahk_...          # or APIHUB_TOKEN=<JWT>
python -m scripts.extract_query --db DW --query eo_detail \
    -p frozen_date=2026-08-01 --batch-size 10000 --out ./eo_detail.csv
```

- Walks `fetch_mode=batch` and writes CSV incrementally, so neither client nor server holds the whole set (`fetch_mode=all` would build one JSON document for every row first)
- `--format` inferred from the `--out` suffix: `csv` (stdlib, streaming), `parquet` / `xlsx` (buffered; need `pandas` plus `pyarrow` / `openpyxl`, which are not installed by default)
- The server recomputes `COUNT(*)` per batch, so per-request cost is nearly flat in batch size — prefer large batches. Measured: 139k rows × 89 columns in ~103 s at `--batch-size 10000`
- Exit codes: `0` ok, `1` API error, `2` bad usage, `3` missing optional output library

### 9. Rate Limiting
- In-memory sliding window, one bucket per caller: per API key (honouring that key's own `rate_limit_per_minute`), per username for human callers, per client IP for anonymous requests
- **Process-local** — it does not coordinate across API instances or Celery workers

### 10. Database Connection Status (`/api/v1/databases/status`)
- Live per-database connectivity check — `Online` (with latency), `Offline` (with the raw driver error), or `Timeout`
- Surfaced in the Admin dashboard's Databases card as a **Status** column with a **Refresh** button
- Every alias is probed concurrently under a **single shared timeout budget**, so N unreachable databases still cost one `DB_HEALTH_CHECK_TIMEOUT`, not N of them
- The probe SQL comes from the dialect layer (`SELECT 1 FROM DUAL` on Oracle, `SELECT 1` elsewhere)
- **Superuser only** — it opens real connections and returns raw driver error text. Other users see the dashboard normally, with the column showing `—`

### 11. API Usage Report (`/api/v1/audit`)

Answers "who called which API, on which data source, how often" and "what exactly did they query", from the per-request audit trail in `api_hub_audit_logs`.

- `GET /audit/usage` — statistics with a free choice of grouping dimensions (`group_by=user,db,api,day`), filtered by date range / user / data source / API / `action_type`. Returns per-group `calls`, `success`, `failed`, `avg_duration_ms`, `max_duration_ms`, `total_rows`, `last_used_at` plus grand totals. `page_size` ≤ 200
- `GET /audit/logs` — the individual requests behind any group, newest first, each carrying the **actual request parameters**. `page_size` ≤ 100
- `GET /audit/logs/{id}` — one request with its parameters always inlined, however large
- `GET /audit/filters` — distinct users / data sources / API names seen in a date range, for the UI's dropdowns
- Admin UI: **API Usage Report** under Operations — statistics table with sortable metric columns, then drill-down to the detail list where each row expands to its parameter JSON with a Copy button
- **Superuser only** — the report exposes every caller's request parameters
- Statistics are computed with a live `GROUP BY` (no pre-aggregated table), backed by composite indexes on `(principal|db_alias|action|action_type, ts)`

Requests are tagged with an `action_type` so data access can be told apart from admin operations: `query`, `procedure`, `write`, `definition` (SQL definition CRUD) and `report` (the report's own reads). `GET /audit/usage` and `/audit/logs` default to `query,procedure,write`.

> **Security note:** request parameters are recorded **unmasked and untruncated** by design. Any value a caller passes as a bind parameter lands in `api_hub_audit_logs.params`, and database backups carry it. The table is readable only by superusers, but treat backups accordingly. Table growth is bounded by `AUDIT_RETENTION_DAYS` plus `python -m scripts.purge_audit_logs` — nothing prunes it automatically.

### 12. MCP Tool Catalog View (`/api/v1/mcp/tools`)

Answers "which SQL definitions actually reach an LLM as MCP tools, and which quietly do not".

The MCP server derives one tool per (data source, SQL definition) named `query_<alias>_<definition>` — lowercased, with anything outside `[a-zA-Z0-9_-]` folded to `_`, capped at 64 characters (the limit MCP tool names must satisfy). When two definitions derive the same name it **keeps the first and silently drops the rest**, in its own process, with no log line and no trace in the REST API. A dropped definition looks healthy in the Admin UI and serves fine over REST while being invisible to every LLM.

- `GET /mcp/tools` — every (alias, definition) pair with its derived tool name, whether it is `exposed`, which definition `shadowed_by` it if not, what it `shadows`, and whether the name was `truncated`
- Admin UI: **MCP Tools** under Operations — the same table with a filter box and a "problems only" toggle
- **Superuser only** — it spans every alias, so it would otherwise let a caller enumerate definitions they hold no `query:<name>` permission for
- **Read-only by design.** The MCP server is a separate process configured through `MCP_*` env vars, so there is nothing here to write to; this reports, it does not configure

Three ways names collide: character folding (`EO Detail` = `EO_DETAIL` = `eo_detail`), the ambiguous `_` between alias and name (`pg_test`+`employees` = `pg`+`test_employees`), and truncation at 64 characters — which bites hardest when the distinguishing part is a suffix like `_v2` / `_v3`. Names come from `mcp_server/naming.py`, the same module the MCP server itself uses, so the view cannot drift from what is really served.

> If the MCP server sets `MCP_DB_ALIASES` it exposes a subset of what this lists. That setting belongs to the MCP process and the Hub cannot read it.

### 13. Client Tooling for LLM Callers

The Hub serves definitions; these are the client-side pieces that make them usable from an agent. All three are **client-side** — none is part of the server image's runtime.

**Period cache — `scripts/eo_cache.py`**

Caches a query result per frozen period, so a closed period is fetched once rather than on every question. Frozen periods are immutable, so this is safe by construction and needs no invalidation policy — apart from one rule it enforces itself.

```bash
export APIHUB_URL=https://<host>/apihub
export APIHUB_API_KEY=ahk_...          # or APIHUB_TOKEN=<JWT>

python -m scripts.eo_cache fetch --query eo_red_items \
    -p frozen_date=2026-06-26 -p compare_date=2026-05-29 -p ou=SP
python -m scripts.eo_cache status
python -m scripts.eo_cache clear --query eo_red_items     # or --all
python -m scripts.eo_cache fetch ... --refresh            # force one refetch
```

- **The latest period is never cached.** The script reads `eo_max_frozen_date` on every run and stores a result only when every date parameter is strictly older. It is re-run and corrected upstream, so caching it would go stale exactly when someone is most likely to be reading it
- Requests carrying no date parameter sweep the latest period by construction and are always fetched live
- Entries are gzipped JSON under `~/.cache/apihub-eo` (`EO_CACHE_DIR`), written atomically; a corrupt entry is treated as a miss
- Refuses results above `--max-rows` (default 50,000) and points at `scripts/extract_query.py` — one period of `eo_detail` is ~139k rows and belongs in a file, not a cache entry or a context window

**Agent skills — `.claude/skills/`**

Plain-Markdown `SKILL.md` files, no Claude Code-specific features, so any agent can consume them as a system prompt.

- `eo-analysis` — how to read the E&O tools correctly. Tool selection, period pinning, and the traps that turn correct numbers into wrong conclusions (true grains, non-additive columns, ratios that must be read by amount rather than by row count)
- `new-sql-definition` — how to author a definition that satisfies both REST and MCP. Covers the failure modes nothing enforces: derived tool names that truncate or collide silently, and the subquery wrapper that makes CTEs unusable and inline `ORDER BY` invalid on MSSQL

**Setup guide — `docs/eo-mcp-setup.md`**

End-user instructions for connecting Claude Code / Claude desktop / Hermes to the MCP endpoint and installing the skill, including the account prerequisite (`SSO_AUTOCREATE=false` means an account must exist before SSO will work) and a connection-failure triage table. It reproduces the `eo-analysis` skill in full for clients with no skill mechanism; `tests/test_docs_skill_sync.py` fails the build if that copy drifts from the original.

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

### Maintenance commands

```bash
# Re-run after upgrading — idempotent, also adds any new columns/indexes
python -m scripts.init_db

# Trim the audit trail. Nothing does this automatically; schedule it.
python -m scripts.purge_audit_logs --dry-run          # report only
python -m scripts.purge_audit_logs                    # uses AUDIT_RETENTION_DAYS
python -m scripts.purge_audit_logs --days 90
```

### Deploying to the ISAP platform gateway

To run behind the shared ISAP nginx gateway at `/apihub/`, use the
self-registering stack in [`deploy/apihub/`](deploy/apihub/README.md). It creates
its DB in `isap-postgres`, uses `isap-redis` for Celery, and drops its own
nginx route — no edits to the `isap-infrastructure` repo. Set `ROOT_PATH=/apihub`
so the app and admin UI generate prefix-aware URLs. A parallel test/UAT stack
lives in [`deploy/apihub-test/`](deploy/apihub-test/README.md) (route
`/apihub-test/`, DB `apihub_test`, Redis logical DBs 3/4/5).

## Adding a New Query Definition

### Via Admin UI (Recommended)

1. Open `http://localhost:8000/admin` and log in as a superuser
2. Navigate to **SQL Definitions** page — groups are listed on the left (sorted by name), the definitions in the selected group on the right (also sorted by name)
3. Click **+ New Definition**
4. Fill in the group, then name, description, SQL, permission, and parameters (JSON). Name is prefilled as `<group>_` and permission as `query:<name>`; edit either and it stops being derived.
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

### Enabling and disabling a definition

Every definition carries an `enabled` flag so one can be taken out of service without deleting it — useful for a query that is misbehaving, or one that is too slow or too verbose to belong in an LLM's tool list.

Disabled means off everywhere: execution answers `404 ... is disabled` (worded distinctly from `not found`, so an operator sees the reason), the definition drops out of `GET /{db}/queries/definitions`, and it therefore disappears from the MCP tool catalog, which is built from that list.

```bash
curl -X PUT "$APIHUB_URL/api/v1/DW/queries/definitions/eo_detail" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# still visible to whoever manages them
curl "$APIHUB_URL/api/v1/DW/queries/definitions?include_disabled=true" -H "..."
```

- Admin UI: an Enable/Disable button on both the list and the detail page; disabled rows stay listed but dimmed, so they can be switched back on
- `GET /api/v1/mcp/tools` keeps reporting them with `enabled: false` and `exposed: false` — that view exists to explain why a definition is not reaching an LLM, and "switched off" is one of those reasons
- A disabled definition does not claim its MCP tool name, so it cannot shadow another definition competing for the same one
- Defaults to enabled, including for rows that predate the column — disabling is always explicit

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
| `DB_HEALTH_CHECK_TIMEOUT` | Total seconds for `GET /databases/status` — shared across all aliases, not per-alias | `5.0` |
| `AUDIT_RETENTION_DAYS` | Default retention window for `python -m scripts.purge_audit_logs` | `365` |
| `AUDIT_PARAMS_INLINE_LIMIT` | Bytes of serialized params inlined in `GET /audit/logs`; larger ones are omitted and fetched per row from `GET /audit/logs/{id}` | `4096` |

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
│   ├── write_service.py     # DML operations
│   ├── db_health_service.py # Live connectivity checks for data sources
│   └── mcp_catalog_service.py # Read-only projection of the MCP tool catalog
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
│   ├── mcp.py               # MCP tool catalog view (superuser only)
│   └── health.py            # Health check
└── sql_definitions/         # YAML query definitions (fallback/import source)
    ├── employees.yaml
    ├── departments.yaml
    └── inventory_view.yaml

mcp_server/                  # MCP server — separate process, proxies the REST API only
├── catalog.py               # Dynamic tool catalog (one tool per alias × definition)
├── naming.py                # Tool-name derivation, shared with the Hub's catalog view
├── client.py                # REST API HTTP client
└── config.py                # MCP_* settings

scripts/                     # Operational and client-side tools (not in the server runtime)
├── init_db.py               # Create tables, backfill columns/indexes, seed the admin user
├── migrate_sql_def_db_alias.py   # One-time: backfill db_alias on pre-existing definitions
├── backfill_sql_def_group.py     # One-time: derive group_name from the definition name
├── purge_audit_logs.py      # Trim the audit trail (schedule it; nothing runs it automatically)
├── register_isap_app.py     # Register this instance's redirect_uris with isap-auth
├── extract_query.py         # Client-side: stream a full result set to CSV/Parquet/XLSX
└── eo_cache.py              # Client-side: per-frozen-period result cache (never caches the latest)

.claude/skills/              # Agent skills — plain Markdown, usable by any agent
├── eo-analysis/SKILL.md     # How to read the E&O tools without drawing wrong conclusions
├── new-sql-definition/SKILL.md   # How to author a definition valid for both REST and MCP
└── update-spec/SKILL.md     # Keep README + system_specification.md in step with the code

docs/
├── system_specification.md  # Full specification (Traditional Chinese)
└── eo-mcp-setup.md          # End-user guide: connect Claude/Hermes, install the skill

examples/
└── read_rd_tc_all.py        # Client example: auth + execute a query as a scoped reader account
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v
```
