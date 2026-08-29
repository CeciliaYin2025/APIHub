# AGENTS.md

This file provides guidance to Codex when working with code in this repository. It mirrors `CLAUDE.md` — keep the two in sync.

## Project Overview

Oracle API Hub is a FastAPI-based REST gateway that exposes **Oracle (11g+), PostgreSQL, and Microsoft SQL Server** resources as REST APIs through a unified adapter + dialect layer. The "Oracle" in the name is historical — a single API Hub instance can serve endpoints backed by any mix of the three engines. It uses DB-managed SQL definitions with Admin UI CRUD (YAML import supported for migration), engine-specific drivers (`oracledb`, `psycopg2-binary`, `pymssql`) for direct data-source access, an internal PostgreSQL + SQLAlchemy ORM store for user/permission/SQL-definition management, and Celery+Redis for async task processing.

## Commands

```bash
# Install (dev mode — pulls pytest/ruff/flower from the [dev] extra)
pip install -e ".[dev]"

# Run the app
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Run all Python tests — tests/e2e is EXCLUDED via norecursedirs, run it separately
pytest -v

# MCP server tests (plain pytest, no live server needed)
pytest tests/mcp -v

# Admin UI e2e (Playwright + throwaway Postgres container; needs Docker + Node 24+)
cd tests/e2e && npm install && npx playwright install chromium && npm test

# Lint / format (ruff; config in pyproject.toml)
ruff check app/ tests/ mcp_server/
ruff format app/ tests/ mcp_server/

# Initialize DB tables and default admin user
python -m scripts.init_db

# Backfill db_alias on pre-existing SQL definition rows (one-time migration)
python -m scripts.migrate_sql_def_db_alias

# Trim the audit trail (nothing does this automatically; schedule it)
python -m scripts.purge_audit_logs [--days N] [--dry-run]

# Bulk-extract a definition to a local file (client-side; needs APIHUB_URL +
# APIHUB_API_KEY or APIHUB_TOKEN)
python -m scripts.extract_query --db DW --query eo_detail -p frozen_date=2026-08-01 --out out.csv

# Register this instance with isap-auth (no-op without a bootstrap token)
python -m scripts.register_isap_app

# Same init inside Docker
docker-compose exec api python -m scripts.init_db

# Celery workers
celery -A app.tasks.celery_app worker -Q write -c 4
celery -A app.tasks.celery_app worker -Q procedure -c 2
celery -A app.tasks.celery_app flower --port=5555
```

## Architecture

**Layered design**: Middleware → Router → Service → Data Access. `app/main.py` owns the lifespan (init pools, load SQL registry).

- **MCP server** (`mcp_server/`): standalone MCP process exposing SQL definitions as
  dynamic tools. Thin HTTP proxy over the REST API — it **never imports `app/`** or touches
  DBs directly. stdio (`python -m mcp_server`) + Streamable HTTP
  (`uvicorn mcp_server.http_app:app`). Settings: `MCP_*` env vars / `.env.mcp`.
  Tool names come from `mcp_server/naming.py` — `query_<alias>_<definition>`, folded to
  `[a-z0-9_-]` and capped at 64 chars. Collisions are resolved by **first-wins, silently**
  (`catalog.py`), so a definition can be healthy over REST yet invisible to every LLM.
  `GET /api/v1/mcp/tools` + the Admin UI **MCP Tools** page report that; both derive names
  from `naming.py` rather than re-implementing the rules, and both are **read-only** — the
  MCP server's own config lives in another process, so there is nothing to write to.

### Data Source Databases vs Platform Store

Two distinct database roles, do not confuse them:

- **Data source databases** (Oracle / PostgreSQL / MSSQL, configured via `app/database_definitions/*.yaml`): the targets the API exposes. All dynamic SQL execution (queries, writes, procedures). `DatabaseRegistry` manages one pool per alias; each alias has a `db_type` that selects an adapter from `app/db/adapters/` and a dialect from `app/db/dialects/`. Per-alias password overrides via `DB_{ALIAS_UPPER}_PASSWORD` env vars.
- **Platform store** (internal PostgreSQL, configured via `POSTGRES_*` env vars, accessed via SQLAlchemy ORM in `app/db/session.py`): user/permission management (`api_hub_users`, `api_hub_roles`, `api_hub_permissions`) and SQL definitions (`api_hub_sql_definitions`). Always PostgreSQL — not pluggable.

### Multi-Database Routing

Database-scoped endpoints use `/{db}/` path prefix (e.g., `/{db}/queries/execute`), where `{db}` is the YAML `alias`. Legacy single-DB env vars (`ORACLE_DSN`, etc.) auto-create a `default` alias for backward compatibility when no YAML is present.

Adding or changing a data source: see `app/database_definitions/CLAUDE.md` (password env vars, no hot-reload, compose mounts).

### Dialect Layer

`app/db/dialects/{oracle,postgresql,mssql}_dialect.py` isolate engine differences so the same SQL definition can target any engine:
- **Pagination**: Oracle wraps with `ROWNUM`; PostgreSQL/MSSQL use `LIMIT/OFFSET` (or `OFFSET ... FETCH NEXT` for MSSQL)
- **Bind conversion**: SQL definitions are written with Oracle-style `:name` binds; PG/MSSQL dialects rewrite to `%(name)s` for psycopg2/pymssql at execution time
- **Procedure/function calls**: PL/SQL `BEGIN ... END;` vs PG `CALL`/`SELECT func()` vs MSSQL `EXEC`
- **Schema introspection**: `ALL_TAB_COLUMNS` vs `information_schema.columns` vs `sys.columns`

When adding engine-specific behavior, extend the `Dialect` base class — never branch on `db_type` inside services.

### Query Engine — 6 Fetch Modes

The query service (`app/services/query_service.py`) supports: `page` (dialect-aware pagination, default), `all`, `batch` (cursor-style iteration), `top_n`, `one` (single row or 404), `count` (row count only, no data). `app/schemas/query.py` is the single source of truth for the mode names, their companion fields and value ranges — keep any UI or doc copy in sync with it.

`page` and `batch` issue two statements per request — a `COUNT(*)` and the page itself — so walking a definition used to recompute the same count once per page (~60% of each request on an unfiltered 45k-row `eo_detail`). `app/services/count_cache.py` memoises it per `(db_alias, count_sql, binds)` for `QUERY_COUNT_CACHE_TTL_SECONDS` (default 60). `count` and `all` deliberately stay live. Because `batch`'s `has_more` derives from `total`, rows inserted mid-walk stay invisible until the entry expires — paging was never atomic, but be explicit about it. The cache is process-local; `tests/conftest.py` clears it between tests.

### SQL Definition System

SQL definitions are stored in PostgreSQL (`api_hub_sql_definitions` table) as the primary source. At startup, `SqlRegistry` loads from DB; if the DB table is empty it falls back to YAML files in `app/sql_definitions/`. A one-time `POST /api/v1/queries/definitions/import-yaml` endpoint imports existing YAML definitions into the database. Full CRUD is available via API (`GET/POST/PUT/DELETE /{db}/queries/definitions`) and the Admin UI. The in-memory registry is automatically refreshed after any CRUD operation.

Two things about the model (`app/models/sql_definition.py`) that are easy to miss:

- **Definitions are scoped per data source**: unique constraint is `(name, db_alias)`, so the same name can exist with different SQL under two aliases. Every definition endpoint is under `/{db}/`, and the registry is keyed accordingly.
- **`order_by`** is a JSONB list of `{column, direction}` applied by the Hub in its own wrapper (not baked into the definition's SQL), so ordering works uniformly across engines including MSSQL. Columns are validated against a strict identifier regex in `_build_order_by()`; never interpolate user input there.

### Request Pipeline — Auth, Rate Limiting, Audit

Middleware is registered in `app/main.py` with a deliberate order (`add_middleware` stacks last-added = outermost). Outer→inner: CORS → `AuditMiddleware` → `AuthContextMiddleware` → `RateLimiterMiddleware` → `ConcurrencyLimiterMiddleware` → routers.

- **`AuthContextMiddleware`** resolves the caller **once per request** into `request.state.principal` (`app/services/auth_service.py::resolve_principal`). A `Principal` is `type="user"` (password JWT), `type="api_key"` (service account), or `type="sso_user"` (isap-auth OAuth token), all three carrying a local `User` for RBAC. Accepted credentials: `X-API-Key: ahk_...`, `Authorization: Bearer ahk_...`, `Authorization: Bearer <JWT>`, and — when `OAUTH_RS_ENABLED` — `Authorization: Bearer <isap-auth access token>`. A present-but-invalid API key raises `ApiKeyError` and short-circuits with JSON; a missing/invalid JWT leaves `principal=None` for the dependency layer to reject.
- **OAuth branch** (`app/services/oauth_rs.py`): isap-auth issues *opaque* tokens, so validation is an RFC 7662 introspection call, cached 60s both ways — that TTL is also the revocation lag. It runs **only after `resolve_principal` returns None**, and is `await`ed in the middleware rather than folded into the synchronous `build_principal`, which would block the event loop on the authorization server. Only the identity crosses over: authorization always comes from local roles, never from a token claim. Same contract as `mcp_server/oauth.py`, minus the identity-bridge hop.
- **`RateLimiterMiddleware`** is an in-memory sliding window keyed per principal — `api_key:{id}` (with that key's `rate_limit` override), `user:{username}` for humans, `ip:{get_client_ip(...)}` for anonymous. Do not key user traffic on the raw client IP: behind the gateway that is one shared bucket for everyone. It is **process-local** — it does not coordinate across API instances or workers.
- **`ConcurrencyLimiterMiddleware`** caps how many requests one principal may have **in flight** (`MAX_CONCURRENT_REQUESTS_PER_PRINCIPAL`, default 4), waits `CONCURRENCY_WAIT_SECONDS` (default 5) for a slot, then returns 429 + `Retry-After`. The rate limiter counts request *starts*, so it cannot stop one caller holding eight slow queries open at once — which is exactly how a single MCP client paging an unfiltered definition took 99.5% of server time on 2026-08-20/24. Both middlewares bucket callers through `app/middleware/principal_key.py::principal_bucket_key` so they throttle the same caller under the same key; keep new per-principal limits on that helper. `/admin`, `/docs`, `/static` and friends are exempt (`EXEMPT_PREFIXES`) because the SPA loads assets in parallel. Also process-local: with N API instances the real cap is N × the setting.
- **`AuditMiddleware`** writes one structured log line plus one `api_hub_audit_logs` row per request, best-effort (failures are swallowed, never affecting the response). Client IP honours `TRUSTED_PROXIES`. It runs *after* `call_next`, so it cannot read the consumed request body — routers hand over the details by calling `app/middleware/audit.py::record_audit(request, action_type, action, params, result_rows)`, which stashes them on `request.state`. When you add an endpoint worth reporting on, call `record_audit` there; nothing else picks it up automatically.

Accounts carry two independent flags: `is_service_account` (API-key only) and `auth_source` (`local`/`sso`). `auth_source='sso'` blocks password login and password resets; it deliberately does **not** block SSO for `local` accounts, because every pre-existing row was backfilled to `local` (`scripts/init_db.py`) and gating that side would lock those users out. A service account cannot be `sso`.

Routers still enforce authorization per endpoint via `PermissionChecker(resource, action)` — e.g. `query:<name>`, `procedure:<name>`, `table:<table>`.

Three SSO entry points share `app/services/sso_service.py::resolve_sso_user`, so the provisioning policy (`SSO_AUTOCREATE`, with `MCP_BRIDGE_AUTOCREATE` kept as an alias) cannot drift between them: `POST /api/v1/auth/sso-token` (the MCP identity bridge, guarded by a shared secret), `GET /api/v1/auth/sso/callback` (the Admin UI, OAuth 2.1 authorization code + PKCE, guarded by a state cookie against login CSRF), and the OAuth branch in `AuthContextMiddleware` (guarded by isap-auth introspection). The Admin UI routes 404 unless `ADMIN_SSO_ENABLED` is on, and hand the minted JWT to the SPA in a URL fragment — never a query parameter, which would reach nginx logs. The isap-auth refresh token stays server-side in an httpOnly `SameSite=Strict` cookie scoped to `/auth/sso`; `POST /auth/sso/refresh` rotates it and returns a new JWT, which is what keeps a session alive past `ACCESS_TOKEN_EXPIRE_MINUTES`. Rotation means the response's new token MUST replace the stored one — isap-auth revokes the whole family on replay. `scripts/register_isap_app.py` registers this app's `redirect_uris` with isap-auth as a compose one-shot; its bootstrap token lives in `deploy/*/.env.isap`, never in `.env`.

### API Usage Report

`/api/v1/audit/*` (`app/api/v1/audit.py` + `app/services/audit_report_service.py`) reports on the audit trail: `usage` aggregates by any combination of user / db / api / day, `logs` lists the individual requests, `logs/{id}` returns one with full params, `filters` feeds the UI dropdowns. **Superuser only** — the report exposes every caller's request parameters. Statistics are a live `GROUP BY`, not a pre-aggregated table.

Two things that are easy to get wrong here:

- **`params` is stored unmasked and untruncated** — a deliberate decision, so do not add masking or truncation on the write path. Consequently nothing bounds table growth except `AUDIT_RETENTION_DAYS` plus `python -m scripts.purge_audit_logs`, which is manual/scheduled and not a Celery beat job. `AUDIT_PARAMS_INLINE_LIMIT` only caps API response size, never what is stored.
- **`action_type`** (`query` / `procedure` / `write` / `definition` / `report`) is what separates data-access volume from admin operations; `usage` and `logs` default to `query,procedure,write`. A new endpoint without an `action_type` is invisible to the report.

Aggregation depends on PostgreSQL-only constructs (`JSONB`, `COUNT(*) FILTER`, `date()`), and `pytest` has no live Postgres — so `tests/test_audit_report_service.py` covers parsing/validation and compiled-SQL shape, and the **numeric** correctness lives in `tests/e2e/usage-report.spec.js`.

### Bulk Extraction

`scripts/extract_query.py` is the bulk counterpart to the MCP tools: MCP returns rows into an LLM's context, which does not fit a six-figure row count, so this walks the REST API's `batch` fetch mode and streams to CSV (or buffers to Parquet/XLSX). It is a **client-side** tool — it talks to the API over HTTP and is not part of the server image's runtime.

Two things worth knowing before changing it: `batch` is used rather than `all` because `all` builds one JSON document for every row before the client sees anything (~105 MB for a 139k-row set), and `_fetch_batch` recomputes `COUNT(*)` on every batch, so per-request cost is nearly flat in batch size — larger batches are dramatically cheaper, which is why the default is 5000 of the allowed 10000.

### Admin UI

Vanilla-JS SPA at `app/admin/`, served at `/admin` with no build step. Conventions and its e2e coverage: see `app/admin/CLAUDE.md`.

### Deployment Behind a Reverse Proxy

`ROOT_PATH` (e.g. `/apihub`) makes FastAPI generate correct URLs and is injected into the Admin UI as `{{BASE_PATH}}`; `tests/test_root_path.py` guards this. `deploy/apihub/` is the ISAP gateway stack and `deploy/apihub-test/` the test/UAT stack, each with its own compose file and `.env`.

### Async Task Processing

Celery with Redis broker. Two dedicated queues: `write` and `procedure`. Tasks retry up to 3 times with exponential backoff. `prefetch_multiplier=1` to protect DB connections.

### Temporary Files

測試資料、API 回應暫存檔等放在 `temp/` 目錄下，該目錄已加入 `.gitignore`，不會被版控追蹤。

## Code Conventions

- **Python**: 3.10+, line length 120, linted and formatted with ruff (`[tool.ruff]` in `pyproject.toml`)
- **Parameterized queries only**: All SQL definitions use Oracle-style bind variables (`:param_name`), never string interpolation. The dialect layer rewrites binds to `%(param_name)s` for PostgreSQL/MSSQL automatically — definitions stay engine-agnostic.
- **Table name validation**: Strict regex in `write_service.py` (per-dialect) to prevent injection
- **Auth**: JWT (HS256) with RBAC. Permission model is `resource:action` with wildcard support
- **Config**: All settings via environment variables through Pydantic Settings (`app/core/config.py`). Add new settings there with a default rather than reading `os.environ` directly.
- **Agent instruction files**: this file mirrors `CLAUDE.md`. When changing project-wide guidance in either, mirror it to the other.
- **Docs**: `README.md` and `docs/system_specification.md` describe the shipped behaviour; update them when adding features.
