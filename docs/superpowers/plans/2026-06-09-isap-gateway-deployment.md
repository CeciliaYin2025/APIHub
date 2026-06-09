# ISAP Gateway Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Oracle API Hub onto the shared ISAP nginx gateway at `https://isap.sercomm.com/apihub/`, self-registering against shared `isap-postgres` and `isap-redis` without editing the `isap-infrastructure` repo.

**Architecture:** Add a `ROOT_PATH` setting that makes the FastAPI app and its admin SPA aware of a path prefix (default empty = unchanged standalone behavior). Add a `deploy/isap/` self-registering compose stack (per `CONTRACT.md`): one-shot init containers create the platform DB and seed it, a route-register container drops the nginx conf and reloads the gateway, and long-running `api` + Celery workers join the shared docker bridges.

**Tech Stack:** FastAPI / uvicorn, Pydantic Settings, Celery + Redis, PostgreSQL, Docker Compose, nginx (gateway, not edited here), pytest.

---

## File Structure

App changes (make the app prefix-aware; all default to no-op when `ROOT_PATH=""`):
- `app/core/config.py` — add `root_path` setting.
- `app/main.py` — pass `root_path` to FastAPI; inject base path when serving the admin `index.html`.
- `app/admin/index.html` — assets + a base-path script tag derived from a placeholder.
- `app/admin/js/app.js` — derive `API` base from `window.__BASE_PATH__`.
- `tests/test_root_path.py` — new tests for prefix awareness.

Deployment artifacts (new, all under `deploy/isap/`; none edit the infra repo):
- `deploy/isap/db-init/create-database.sh`
- `deploy/isap/gateway-conf/apihub.conf`
- `deploy/isap/docker-compose.yml`
- `deploy/isap/.env.example`
- `deploy/isap/README.md`

---

## Task 1: Add `root_path` setting

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_root_path.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_root_path.py
from app.core.config import Settings


def test_root_path_defaults_to_empty():
    s = Settings()
    assert s.root_path == ""


def test_root_path_reads_from_env(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "/apihub")
    s = Settings()
    assert s.root_path == "/apihub"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_root_path.py::test_root_path_defaults_to_empty -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'root_path'`

- [ ] **Step 3: Add the setting**

In `app/core/config.py`, add this line in the `# API` block (right after `api_prefix`):

```python
    api_prefix: str = "/api/v1"
    root_path: str = ""  # URL prefix when served behind a reverse proxy (e.g. /apihub)
    debug: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_path.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py tests/test_root_path.py
git commit -m "feat: add ROOT_PATH setting for reverse-proxy prefix"
```

---

## Task 2: Make FastAPI + admin SPA prefix-aware

**Files:**
- Modify: `app/main.py`
- Modify: `app/admin/index.html`
- Modify: `app/admin/js/app.js`
- Test: `tests/test_root_path.py`

The admin `index.html` currently hard-codes absolute URLs that break behind a
prefix. We replace them with a literal placeholder token `__BASE_PATH__` and
substitute it at serve time from `settings.root_path`. When `root_path` is `""`
the token becomes `""`, restoring the original absolute-from-root URLs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_root_path.py`:

```python
from fastapi.testclient import TestClient


def _client(monkeypatch, root_path):
    monkeypatch.setenv("ROOT_PATH", root_path)
    # reload modules so the new env is picked up
    import importlib
    import app.core.config as config_mod
    importlib.reload(config_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_admin_index_injects_base_path(monkeypatch):
    client = _client(monkeypatch, "/apihub")
    html = client.get("/admin").text
    # placeholder must be gone, prefixed URLs present
    assert "__BASE_PATH__" not in html
    assert '/apihub/admin/js/app.js' in html
    assert 'window.__BASE_PATH__ = "/apihub"' in html


def test_admin_index_empty_base_path_default(monkeypatch):
    client = _client(monkeypatch, "")
    html = client.get("/admin").text
    assert "__BASE_PATH__" not in html  # placeholder substituted, not left raw
    assert '"/admin/js/app.js"' in html
    assert 'window.__BASE_PATH__ = ""' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_path.py -k admin_index -v`
Expected: FAIL (placeholder `__BASE_PATH__` not present / not substituted yet)

- [ ] **Step 3: Edit `app/admin/index.html`**

Replace the stylesheet link in `<head>`:

```html
  <link rel="stylesheet" href="__BASE_PATH__/admin/css/style.css">
```

Add, immediately before the closing `</head>` tag is not required; instead
replace the final script tag near `</body>`:

```html
<script>window.__BASE_PATH__ = "__BASE_PATH__";</script>
<script src="__BASE_PATH__/admin/js/app.js"></script>
</body>
```

(There are exactly two asset URLs — the css `<link>` and the js `<script src>` —
plus the new inline base-path script. All three use the `__BASE_PATH__` token.)

- [ ] **Step 4: Edit `app/admin/js/app.js`**

Change line 8 from:

```javascript
const API = '/api/v1';
```

to:

```javascript
const API = (window.__BASE_PATH__ || '') + '/api/v1';
```

- [ ] **Step 5: Edit `app/main.py`**

Pass `root_path` to FastAPI — change the `app = FastAPI(...)` block:

```python
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
    root_path=settings.root_path,
)
```

Replace the admin SPA handler at the bottom of the file:

```python
@app.get("/admin")
@app.get("/admin/{rest_of_path:path}")
def admin_spa(rest_of_path: str = ""):
    """Serve the admin SPA index.html, substituting the base path prefix."""
    html = (_ADMIN_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__BASE_PATH__", settings.root_path)
    return HTMLResponse(content=html)
```

Add `HTMLResponse` to the responses import near the top:

```python
from fastapi.responses import FileResponse, HTMLResponse
```

- [ ] **Step 6: Run the full prefix test set**

Run: `pytest tests/test_root_path.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Regression — existing tests still pass**

Run: `pytest -q`
Expected: PASS (no regressions). The default `ROOT_PATH=""` keeps standalone behavior identical.

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/admin/index.html app/admin/js/app.js tests/test_root_path.py
git commit -m "feat: make app and admin SPA aware of ROOT_PATH prefix"
```

---

## Task 3: Idempotent database-creation script

**Files:**
- Create: `deploy/isap/db-init/create-database.sh`

- [ ] **Step 1: Write the script**

```sh
#!/bin/sh
# Idempotently create the API Hub platform-store database in the shared
# isap-postgres. No-op when the database already exists.
set -e

: "${POSTGRES_HOST:?POSTGRES_HOST not set}"
: "${POSTGRES_PORT:?POSTGRES_PORT not set}"
: "${POSTGRES_USER:?POSTGRES_USER not set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set}"
: "${POSTGRES_DB:?POSTGRES_DB not set}"

echo "Waiting for postgres at $POSTGRES_HOST:$POSTGRES_PORT ..."
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -q; do
  sleep 1
done

export PGPASSWORD="$POSTGRES_PASSWORD"
if psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" | grep -q 1; then
  echo "Database '$POSTGRES_DB' already exists — nothing to do."
else
  echo "Creating database '$POSTGRES_DB' ..."
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c \
    "CREATE DATABASE \"$POSTGRES_DB\""
  echo "Created."
fi
```

- [ ] **Step 2: Mark executable & syntax-check**

Run: `sh -n deploy/isap/db-init/create-database.sh && chmod +x deploy/isap/db-init/create-database.sh`
Expected: no output (valid shell syntax), file made executable.

- [ ] **Step 3: Commit**

```bash
git add deploy/isap/db-init/create-database.sh
git commit -m "feat: add idempotent platform-store DB creation script"
```

---

## Task 4: Gateway route conf

**Files:**
- Create: `deploy/isap/gateway-conf/apihub.conf`

- [ ] **Step 1: Write the conf**

```nginx
# Oracle API Hub — registered route on the ISAP gateway.
# Bare location blocks only (included inside the gateway's server context).
# Uses `set $upstream` lazy resolution so nginx starts even when apihub-api
# is not yet running (per CONTRACT.md).

location = /apihub {
    return 301 /apihub/;
}

location /apihub/ {
    set $apihub_up http://apihub-api:8000;
    rewrite ^/apihub(/.*)$ $1 break;
    proxy_pass $apihub_up;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /apihub;

    proxy_read_timeout 300s;
    proxy_connect_timeout 10s;

    # YAML-definition import upload
    client_max_body_size 50m;
}
```

- [ ] **Step 2: Sanity check brace balance**

Run: `grep -c '{' deploy/isap/gateway-conf/apihub.conf; grep -c '}' deploy/isap/gateway-conf/apihub.conf`
Expected: both counts equal (2 and 2). Full `nginx -t` validation happens at deploy time inside the route-register container (Task 5).

- [ ] **Step 3: Commit**

```bash
git add deploy/isap/gateway-conf/apihub.conf
git commit -m "feat: add nginx gateway route conf for /apihub"
```

---

## Task 5: Deployment compose stack

**Files:**
- Create: `deploy/isap/docker-compose.yml`

- [ ] **Step 1: Write the compose file**

```yaml
# ==============================================================================
# Oracle API Hub — ISAP platform deployment stack
# ==============================================================================
# Self-registers against the shared ISAP infrastructure (CONTRACT.md v1.1):
#   - creates its own DB in isap-postgres (apihub-db-init)
#   - seeds tables + admin user (apihub-migrate)
#   - drops its nginx route and reloads the gateway (apihub-route-register)
#   - api + celery workers join gateway-net / db-net / redis-net
#
# Deploy:  cd deploy/isap && cp .env.example .env && <edit> && docker compose up -d --build
# ==============================================================================

services:
  # ── one-shot: create the platform-store database in shared postgres ───────
  apihub-db-init:
    image: postgres:16-alpine
    container_name: apihub-db-init
    restart: "no"
    env_file: [.env]
    networks: [db-net]
    entrypoint: ["sh", "/db-init/create-database.sh"]
    volumes:
      - ./db-init:/db-init:ro

  # ── one-shot: create tables + seed default admin (idempotent) ─────────────
  apihub-migrate:
    build:
      context: ../..
    image: oracle-api-hub:latest
    container_name: apihub-migrate
    restart: "no"
    env_file: [.env]
    networks: [db-net, redis-net]
    command: ["python", "-m", "scripts.init_db"]
    depends_on:
      apihub-db-init:
        condition: service_completed_successfully

  # ── one-shot: register the gateway route and reload nginx ─────────────────
  apihub-route-register:
    image: docker:27-cli
    container_name: apihub-route-register
    restart: "no"
    volumes:
      - ./gateway-conf/apihub.conf:/conf/apihub.conf:ro
      - ${INFRA_GATEWAY_APPS_DIR:-/opt/isap/gateway-apps}:/dest
      - /var/run/docker.sock:/var/run/docker.sock
    entrypoint: ["sh", "-c"]
    command: |
      set -e
      cp -f /conf/apihub.conf /dest/apihub.conf
      if docker exec isap-gateway nginx -t; then
        docker kill -s HUP isap-gateway 2>/dev/null || true
        echo "apihub route registered and gateway reloaded."
      else
        echo "nginx -t failed; removing conf to avoid breaking the gateway." >&2
        rm -f /dest/apihub.conf
        exit 1
      fi

  # ── long-running: FastAPI app ─────────────────────────────────────────────
  apihub-api:
    build:
      context: ../..
    image: oracle-api-hub:latest
    container_name: apihub-api
    restart: unless-stopped
    env_file: [.env]
    networks: [gateway-net, db-net, redis-net]
    depends_on:
      apihub-migrate:
        condition: service_completed_successfully
      apihub-route-register:
        condition: service_completed_successfully

  # ── long-running: Celery write-queue worker ───────────────────────────────
  apihub-celery-write:
    image: oracle-api-hub:latest
    container_name: apihub-celery-write
    restart: unless-stopped
    env_file: [.env]
    networks: [db-net, redis-net]
    command: ["celery", "-A", "app.tasks.celery_app", "worker", "-Q", "write", "-l", "info", "--concurrency=4"]
    depends_on:
      apihub-api:
        condition: service_started

  # ── long-running: Celery procedure-queue worker ───────────────────────────
  apihub-celery-proc:
    image: oracle-api-hub:latest
    container_name: apihub-celery-proc
    restart: unless-stopped
    env_file: [.env]
    networks: [db-net, redis-net]
    command: ["celery", "-A", "app.tasks.celery_app", "worker", "-Q", "procedure", "-l", "info", "--concurrency=2"]
    depends_on:
      apihub-api:
        condition: service_started

  # ── optional: Flower (no built-in auth — keep OFF the gateway). Uncomment
  #    and tunnel via SSH if you need it.
  # apihub-flower:
  #   image: oracle-api-hub:latest
  #   container_name: apihub-flower
  #   restart: unless-stopped
  #   env_file: [.env]
  #   networks: [redis-net]
  #   command: ["celery", "-A", "app.tasks.celery_app", "flower", "--port=5555"]
  #   ports: ["127.0.0.1:5555:5555"]

networks:
  gateway-net: { external: true }
  db-net:      { external: true }
  redis-net:   { external: true }
```

- [ ] **Step 2: Validate compose syntax (with a throwaway env)**

Run:
```bash
cd deploy/isap && cp .env.example .env 2>/dev/null || true
docker compose config >/dev/null && echo "compose OK"
```
Expected: `compose OK` (the `external: true` networks need not exist for `config`). If docker is unavailable on the authoring machine, skip — validation happens on the Linux host at deploy.

- [ ] **Step 3: Commit**

```bash
git add deploy/isap/docker-compose.yml
git commit -m "feat: add ISAP deployment compose stack"
```

---

## Task 6: Deployment `.env.example`

**Files:**
- Create: `deploy/isap/.env.example`

- [ ] **Step 1: Write the example env**

```bash
# ==============================================================================
# Oracle API Hub — ISAP deployment environment
#   cp .env.example .env  &&  edit every <...> value
# ==============================================================================

# --- Reverse-proxy prefix (MUST match the gateway route /apihub) ---
ROOT_PATH=/apihub

# --- Platform store: shared isap-postgres (v1: shared admin user) -------------
POSTGRES_HOST=isap-postgres
POSTGRES_PORT=5432
POSTGRES_DB=apihub
POSTGRES_USER=isap_admin
POSTGRES_PASSWORD=<shared infra PG_ADMIN_PASSWORD>

# --- Celery / Redis: shared isap-redis ----------------------------------------
# Note isap-redis requires the shared password and uses `default` user.
REDIS_URL=redis://default:<REDIS_PASSWORD>@isap-redis:6379/0
CELERY_BROKER_URL=redis://default:<REDIS_PASSWORD>@isap-redis:6379/1
CELERY_RESULT_BACKEND=redis://default:<REDIS_PASSWORD>@isap-redis:6379/2

# --- JWT ----------------------------------------------------------------------
SECRET_KEY=<openssl rand -base64 48>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# --- Oracle data source (target DB the API exposes) ---------------------------
ORACLE_DSN=<host>:1521/<service>
ORACLE_USER=<user>
ORACLE_PASSWORD=<password>
ORACLE_POOL_MIN=2
ORACLE_POOL_MAX=10
ORACLE_POOL_INCREMENT=1
ORACLE_CLIENT_DIR=

# --- Route-register: host dir the gateway includes confs from -----------------
INFRA_GATEWAY_APPS_DIR=/opt/isap/gateway-apps

# --- API ----------------------------------------------------------------------
API_TITLE=Oracle API Hub
API_VERSION=1.0.0
API_PREFIX=/api/v1
DEBUG=false
RATE_LIMIT_PER_MINUTE=60
SQL_DEFINITIONS_DIR=app/sql_definitions
```

- [ ] **Step 2: Confirm no real secrets committed**

Run: `grep -n changeme deploy/isap/.env.example; grep -nE '<[^>]+>' deploy/isap/.env.example | head`
Expected: no `changeme`; only `<placeholder>` tokens (proves real secrets are not baked in).

- [ ] **Step 3: Commit**

```bash
git add deploy/isap/.env.example
git commit -m "docs: add ISAP deployment .env.example"
```

---

## Task 7: Deployment README

**Files:**
- Create: `deploy/isap/README.md`

- [ ] **Step 1: Write the README**

````markdown
# Deploying Oracle API Hub to the ISAP Gateway

Self-registers against the shared ISAP infrastructure (see that repo's
`CONTRACT.md`). **Does not edit the infra repo.** Reachable at
`https://<GATEWAY_SERVER_NAME>/apihub/` after deploy.

## Prerequisites (on the ISAP Linux host)

- The `isap-infrastructure` stack is already `up` (gateway, postgres, redis).
- External networks exist: `gateway-net`, `db-net`, `redis-net`
  (`docker network ls`).
- `${INFRA_GATEWAY_APPS_DIR}` (default `/opt/isap/gateway-apps`) exists and is
  writable by the deploy user.
- You know the shared `PG_ADMIN_PASSWORD` and `REDIS_PASSWORD` from the infra
  `.env`.

## Deploy

```bash
cd deploy/isap
cp .env.example .env
# Fill every <...> value — shared infra passwords, Oracle DSN, fresh SECRET_KEY.
grep -nE '<[^>]+>' .env   # must return nothing before continuing

docker compose up -d --build
docker compose ps         # apihub-db-init / -migrate / -route-register Exited(0);
                          # apihub-api + celery workers Up
```

## Verify

```bash
# Route registered
ls /opt/isap/gateway-apps/apihub.conf

# Through the gateway (-k: self-signed/intranet cert)
curl -kI  https://<domain>/apihub/api/v1/health
curl -ksS https://<domain>/apihub/admin | grep __BASE_PATH__   # must print nothing
curl -ksS https://<domain>/apihub/admin | grep '/apihub/admin/js/app.js'  # present

# Default admin login: admin / changeme  → CHANGE IT immediately in the UI.
```

## Update

```bash
git pull
docker compose up -d --build      # rebuilds image, recreates containers
```

## Decommission

```bash
docker compose down
rm -f /opt/isap/gateway-apps/apihub.conf
docker exec isap-gateway nginx -s reload
# The 'apihub' database in isap-postgres is left intact — drop manually if desired.
```

## Notes

- **First-login password**: `scripts/init_db` seeds `admin` / `changeme`.
  Change it on first login.
- **Oracle thick mode (11g)**: the root `Dockerfile` installs Instant Client.
  There is a known version-pin mismatch (`LD_LIBRARY_PATH` hard-codes
  `instantclient_21_13`); if Oracle 11g thick mode fails to initialize, align
  the path with the actual unzipped directory. Oracle 12.1+ thin mode is
  unaffected.
- **Shared Redis**: Celery uses logical DBs 0/1/2 on `isap-redis`; key
  isolation is by convention only (CONTRACT v1).
````

- [ ] **Step 2: Commit**

```bash
git add deploy/isap/README.md
git commit -m "docs: add ISAP deployment README"
```

---

## Task 8: Final verification & docs sync

**Files:**
- Modify: `README.md` (project root — add a pointer to the ISAP deploy path)

- [ ] **Step 1: Full test suite**

Run: `pytest -q`
Expected: PASS (all tests, including the new `tests/test_root_path.py`).

- [ ] **Step 2: Lint**

Run: `ruff check app/ tests/`
Expected: no errors (or only pre-existing, unrelated ones).

- [ ] **Step 3: Add a deploy pointer to the project README**

In the root `README.md`, add a short subsection under the existing deployment/Docker
content:

```markdown
### Deploying to the ISAP platform gateway

To run behind the shared ISAP nginx gateway at `/apihub/`, use the
self-registering stack in [`deploy/isap/`](deploy/isap/README.md). It creates
its DB in `isap-postgres`, uses `isap-redis` for Celery, and drops its own
nginx route — no edits to the `isap-infrastructure` repo.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: link ISAP deployment guide from project README"
```

---

## Self-Review Notes

- **Spec coverage:** topology (Tasks 5/6), prefix handling (Tasks 1/2 + conf in Task 4), self-registration db-init/route-register (Tasks 3/4/5), migrate/init_db (Task 5 `apihub-migrate`), shared-service wiring (Task 6), Flower omitted/commented (Task 5), known risks documented (Task 7). All spec sections map to a task.
- **No placeholders:** every code/step is concrete. `<...>` tokens appear only inside `.env.example`/README as deliberate operator-fill values, never in executable plan steps.
- **Type/name consistency:** container names (`apihub-api`, `apihub-db-init`, `apihub-migrate`, `apihub-route-register`, `apihub-celery-write`, `apihub-celery-proc`), the upstream `http://apihub-api:8000`, the conf filename `apihub.conf`, the `__BASE_PATH__` token, and `window.__BASE_PATH__` are used consistently across Tasks 2/4/5/7.
