# Configuring Data Sources (`database_definitions`)

All data-source connections (read + write/procedure targets) are defined in `app/database_definitions/databases.yaml`. The file is a **YAML list** where each entry is one alias; `DatabaseRegistry.load()` (in `app/db/multi_pool.py`) reads it. There is no separate "source" vs "target" concept — an alias is a connection that serves both queries (read) and writes/procedures (write); add multiple aliases for multiple sources/targets.

Per-entry fields (loaded into `DatabaseConfig`):

- `alias` (required): identifier and API path segment. Regex `^[a-zA-Z][a-zA-Z0-9_]{0,63}$`.
- `db_type`: `oracle` (default), `postgresql`, or `mssql` — selects the adapter (`app/db/adapters/`) and dialect (`app/db/dialects/`).
- `description`: shown in `GET /api/v1/databases` and the Admin UI DB selector.
- Oracle: `dsn` (`host:port/service`).
- PostgreSQL / MSSQL: `host`, `port`, `database`.
- Common: `user`, `password`, `pool_min`, `pool_max`, `pool_increment` (Oracle only), `pool_acquire_timeout` (default 10s).
- **`pool_max` is only a real ceiling on MSSQL and PostgreSQL.** Oracle's own pool queues on it; psycopg2's `ThreadedConnectionPool` raises at once; the MSSQL adapter bounds checkouts with a semaphore and, after `pool_acquire_timeout` seconds, refuses with `PoolExhaustedError` → **HTTP 503**. Refusing beats queueing: query endpoints are sync, so each waiter holds one of the process's worker threads, and one saturated alias can otherwise starve every other endpoint including login.

Conventions to follow:

- **No plaintext passwords in the YAML.** Leave `password: ""` and supply it via the env var `DB_{ALIAS_UPPER}_PASSWORD` (e.g. alias `pg_test` → `DB_PG_TEST_PASSWORD`). The env var, when set, overrides the YAML value (`multi_pool.py` `_load_file`). Every alias needs its own var or its connection fails.
- **Definitions load once at startup** (`db_registry.load()` in the lifespan); pools are created lazily on first use. There is **no hot-reload endpoint** for data sources (unlike SQL definitions) — editing `databases.yaml` requires a **process/container restart** to take effect, and the Celery workers must be restarted too (they connect via the same YAML).
- **Both compose stacks bind-mount the directory** so edits need only a restart, not a rebuild: root `docker-compose.yaml` (local dev) and `deploy/apihub/docker-compose.yml` (ISAP gateway; `deploy/apihub-test/` for the test/UAT stack) mount `app/database_definitions` into the `api` and both celery services. When adding an alias there, also add its `DB_*_PASSWORD` to the corresponding `.env`.
