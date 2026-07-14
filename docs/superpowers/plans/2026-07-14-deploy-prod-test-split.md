# Deploy prod/test split + isap→apihub rename — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the single `deploy/isap/` stack into two parallel, self-contained deployments — `deploy/apihub/` (prod) and `deploy/apihub-test/` (test) — mirroring the `e-Budget Management Platform` convention, with the MCP server split into its own add-on compose file.

**Architecture:** Rename `isap`→`apihub` via `git mv`. Split the inline MCP service out of `docker-compose.yml` into `docker-compose.mcp.yml` (+ its own `.env.mcp`, + an auto-registering `*-mcp-route-register` one-shot). Duplicate the whole prod directory as `apihub-test` with test-specific values. Both stacks self-register against the same shared ISAP infra without collision.

**Tech Stack:** Docker Compose, nginx (gateway drop-in confs), FastAPI/Celery images, PostgreSQL (`isap-postgres`), Redis (`isap-redis`).

## Global Constraints

- **No application-code changes.** Only `deploy/**` and living docs (`CLAUDE.md`, `README.md`) change.
- **Preserve history:** rename with `git mv`, never delete+recreate.
- **No plaintext collisions on shared infra:** prod and test must differ in every name that lands on `isap-postgres` / `isap-redis` / `isap-gateway` (container names, `POSTGRES_DB`, Redis DB indices, gateway conf filenames, route prefixes).
- **`MCP_BRIDGE_SECRET`** must be identical between a stack's `.env` (api side) and `.env.mcp` (mcp side).
- **Test route prefix:** `/apihub-test`; **test `POSTGRES_DB`:** `apihub_test` (underscore — it's a DB identifier); **test Redis DBs:** `/3` cache, `/4` broker, `/5` backend.
- **nginx variable names** use underscores only (no hyphens): prod `$apihub_up`; test `$apihub_test_up`.
- **Historical docs** under `docs/superpowers/specs|plans/` are left unchanged.
- Spec: `docs/superpowers/specs/2026-07-14-deploy-prod-test-split-design.md`.

---

### Task 1: Rename `deploy/isap` → `deploy/apihub` (pure `git mv`)

**Files:**
- Move: `deploy/isap/` → `deploy/apihub/` (whole directory)

**Interfaces:**
- Produces: `deploy/apihub/` containing the unchanged former `isap` files. Later tasks edit files under this path.

- [ ] **Step 1: Rename with git**

```bash
cd /c/app/Oracle-api-hub
git mv deploy/isap deploy/apihub
```

- [ ] **Step 2: Verify the move preserved every file**

```bash
git status --short        # expect R (renamed) entries for all 8 files
ls deploy/apihub          # docker-compose.yml .env.example db-init gateway-conf README.md sql_definitions
test ! -d deploy/isap && echo "isap gone OK"
```
Expected: all files show as renamed; `deploy/isap` no longer exists.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor(deploy): rename isap deployment dir to apihub"
```

---

### Task 2: Split MCP out of the prod `docker-compose.yml`

Remove the inline `apihub-mcp` service from `deploy/apihub/docker-compose.yml`; the api + celery services and the three one-shots stay.

**Files:**
- Modify: `deploy/apihub/docker-compose.yml` (delete the `apihub-mcp` service block)

**Interfaces:**
- Produces: a main stack with no MCP service. Task 3 adds `docker-compose.mcp.yml` that references `apihub-api` and `oracle-api-hub:latest` (both still defined here).

- [ ] **Step 1: Delete the `apihub-mcp` service block**

In `deploy/apihub/docker-compose.yml`, remove the entire block from the comment header
`# ── long-running: MCP server (Streamable HTTP, OAuth 2.1 Resource Server) ──`
through the end of the `apihub-mcp:` service (its `depends_on: apihub-api: condition: service_started`), i.e. the current lines:

```yaml
  # ── long-running: MCP server (Streamable HTTP, OAuth 2.1 Resource Server) ──
  # Thin proxy over apihub-api's REST API — never touches the databases
  # directly. Gateway route: deploy/isap/gateway-conf/apihub-mcp.conf.
  apihub-mcp:
    image: oracle-api-hub:latest
    container_name: apihub-mcp
    restart: unless-stopped
    env_file: [.env]
    environment:
      MCP_API_HUB_URL: http://apihub-api:8000
      MCP_OAUTH_ENABLED: "true"
      MCP_PUBLIC_URL: ${MCP_PUBLIC_URL:?MCP_PUBLIC_URL is required for OAuth metadata}
      MCP_AUTH_ISSUER: ${MCP_AUTH_ISSUER:?MCP_AUTH_ISSUER is required for OAuth metadata}
      MCP_INTROSPECTION_URL: http://isap-auth:9997/auth/api/v1/introspect
      MCP_INTROSPECTION_SECRET: ${OAUTH_INTROSPECTION_SECRET}
      MCP_BRIDGE_SECRET: ${MCP_BRIDGE_SECRET:-}
    command: ["uvicorn", "mcp_server.http_app:app", "--host", "0.0.0.0", "--port", "8810"]
    networks: [gateway-net]
    depends_on:
      apihub-api:
        condition: service_started
```

Leave the trailing `# ── optional: Flower ...` comment block and the `networks:` section intact. The `apihub-api` service keeps its `environment:` `MCP_BRIDGE_SECRET` / `MCP_BRIDGE_AUTOCREATE` (the bridge endpoint lives on the api).

- [ ] **Step 2: Fix the stale path comment on `apihub-api`**

The api service's volume comment and header still say nothing about MCP; no change needed there. Confirm no remaining `apihub-mcp` references in this file:

```bash
grep -n "apihub-mcp\|MCP_PUBLIC_URL\|MCP_AUTH_ISSUER" deploy/apihub/docker-compose.yml
```
Expected: no output.

- [ ] **Step 3: Verify the main compose still parses**

```bash
cd deploy/apihub && cp .env.example .env
docker compose config -q && echo "main compose OK"
rm -f .env && cd /c/app/Oracle-api-hub
```
Expected: `main compose OK` (no errors). (`.env` is git-ignored; removed after check.)

- [ ] **Step 4: Commit**

```bash
git add deploy/apihub/docker-compose.yml
git commit -m "refactor(deploy): remove inline MCP service from main compose"
```

---

### Task 3: Add prod `docker-compose.mcp.yml` + `.env.mcp.example`

**Files:**
- Create: `deploy/apihub/docker-compose.mcp.yml`
- Create: `deploy/apihub/.env.mcp.example`

**Interfaces:**
- Consumes: `oracle-api-hub:latest` image and `apihub-api` container (both from `docker-compose.yml`), external `gateway-net`.
- Produces: `apihub-mcp` (port 8810 on gateway-net) + `apihub-mcp-route-register` one-shot that drops `apihub-mcp.conf` into `${INFRA_GATEWAY_APPS_DIR}` and SIGHUPs `isap-gateway`.

- [ ] **Step 1: Create `deploy/apihub/docker-compose.mcp.yml`**

```yaml
# ==============================================================================
# Oracle API Hub — MCP server add-on (Streamable HTTP, OAuth 2.1 Resource Server)
# ==============================================================================
# Optional add-on to docker-compose.yml. Brings up the MCP server and registers
# its gateway route (/apihub/mcp/*). Thin proxy over apihub-api's REST API — it
# never touches the databases or Celery directly.
#
# Prerequisites: the main stack (docker-compose.yml) is built + up, so the
# oracle-api-hub:latest image exists and apihub-api is reachable on gateway-net.
#
# Deploy:
#   cd deploy/apihub && cp .env.mcp.example .env.mcp   # edit every <...> value
#   grep -nE '<[^>]+>' .env.mcp                        # must return nothing
#   docker compose -f docker-compose.mcp.yml up -d
#
# NOTE: only the app-scoped route (apihub-mcp.conf) is auto-registered here. The
# root well-known rewrite (oauth-wellknown-apihub-mcp.snippet.conf) is a SHARED
# gateway file and must be pasted in by hand — see README.md "MCP server".
# ==============================================================================

services:
  # ── one-shot: register the MCP gateway route and reload nginx ──────────────
  apihub-mcp-route-register:
    image: docker:27-cli
    container_name: apihub-mcp-route-register
    restart: "no"
    volumes:
      - ./gateway-conf/apihub-mcp.conf:/conf/apihub-mcp.conf:ro
      - ${INFRA_GATEWAY_APPS_DIR:-/opt/isap/gateway-apps}:/dest
      - /var/run/docker.sock:/var/run/docker.sock
    # Same inlined-script contract as apihub-route-register in docker-compose.yml:
    # the whole script MUST live in this entrypoint list, and shell vars use $$
    # so Compose does not interpolate them.
    entrypoint:
      - sh
      - -c
      - |
        set -e
        DEST=/dest/apihub-mcp.conf
        BAK=/dest/.apihub-mcp.conf.bak
        [ -f "$$DEST" ] && cp -f "$$DEST" "$$BAK" || true
        cp -f /conf/apihub-mcp.conf "$$DEST"
        if docker exec isap-gateway nginx -t; then
          if docker kill -s HUP isap-gateway; then
            echo "apihub-mcp route registered and gateway reloaded."
            rm -f "$$BAK"
          else
            echo "route conf valid but SIGHUP reload of isap-gateway failed." >&2
            exit 1
          fi
        else
          echo "nginx -t failed; restoring previous conf to avoid breaking the gateway." >&2
          if [ -f "$$BAK" ]; then mv -f "$$BAK" "$$DEST"; else rm -f "$$DEST"; fi
          exit 1
        fi

  # ── long-running: MCP server ──────────────────────────────────────────────
  apihub-mcp:
    image: oracle-api-hub:latest
    container_name: apihub-mcp
    restart: unless-stopped
    env_file: [.env.mcp]
    command: ["uvicorn", "mcp_server.http_app:app", "--host", "0.0.0.0", "--port", "8810"]
    # gateway-net only: reaches apihub-api (also on gateway-net) for the REST
    # proxy + identity bridge. No db-net/redis-net (thin HTTP proxy).
    networks: [gateway-net]
    depends_on:
      apihub-mcp-route-register:
        condition: service_completed_successfully

networks:
  gateway-net: { external: true }
```

- [ ] **Step 2: Create `deploy/apihub/.env.mcp.example`**

```bash
# ==============================================================================
# Oracle API Hub — MCP server (docker-compose.mcp.yml) environment
#   cp .env.mcp.example .env.mcp  &&  edit every <...> value
# ==============================================================================
# Consumed only by the apihub-mcp container. MCP_BRIDGE_SECRET here MUST equal
# the MCP_BRIDGE_SECRET in .env — the api side activates the identity-bridge
# endpoint, the mcp side calls it with the same secret.

# REST API base URL (docker-internal; the MCP server is a thin proxy over it)
MCP_API_HUB_URL=http://apihub-api:8000

# --- OAuth 2.1 Resource Server ------------------------------------------------
MCP_OAUTH_ENABLED=true
# Public URL of the MCP resource as clients see it (no trailing slash)
MCP_PUBLIC_URL=https://<GATEWAY_SERVER_NAME>/apihub/mcp
# isap-auth's issuer URL (same platform Authorization Server)
MCP_AUTH_ISSUER=https://<GATEWAY_SERVER_NAME>/auth
# isap-auth introspection endpoint (docker-internal)
MCP_INTROSPECTION_URL=http://isap-auth:9997/auth/api/v1/introspect
# Shared with isap-auth's OWN OAUTH_INTROSPECTION_SECRET — copy it, don't invent.
MCP_INTROSPECTION_SECRET=<same value as isap-auth's OAUTH_INTROSPECTION_SECRET>
# Must be identical to MCP_BRIDGE_SECRET in .env
MCP_BRIDGE_SECRET=<same openssl value as .env MCP_BRIDGE_SECRET>
```

- [ ] **Step 3: Verify the MCP compose parses**

```bash
cd deploy/apihub && cp .env.mcp.example .env.mcp
docker compose -f docker-compose.mcp.yml config -q && echo "mcp compose OK"
rm -f .env.mcp && cd /c/app/Oracle-api-hub
```
Expected: `mcp compose OK`.

- [ ] **Step 4: Commit**

```bash
git add deploy/apihub/docker-compose.mcp.yml deploy/apihub/.env.mcp.example
git commit -m "feat(deploy): add MCP add-on compose with auto-registering route"
```

---

### Task 4: Trim prod `.env.example` (move MCP-server vars to `.env.mcp`)

**Files:**
- Modify: `deploy/apihub/.env.example` (replace the MCP section)

**Interfaces:**
- Produces: a main `.env.example` that keeps only `MCP_BRIDGE_SECRET` + `MCP_BRIDGE_AUTOCREATE` (needed by `apihub-api`), pointing at `.env.mcp` for the rest.

- [ ] **Step 1: Replace the MCP block**

In `deploy/apihub/.env.example`, replace the current block (from `# --- MCP server (apihub-mcp, OAuth 2.1 Resource Server) ---` through the `MCP_BRIDGE_AUTOCREATE=false` line, i.e. lines 44–60) with:

```bash
# --- MCP identity bridge (apihub-api side) ------------------------------------
# The MCP *server* has its own env file now (.env.mcp / .env.mcp.example) used by
# docker-compose.mcp.yml. Only the two bridge vars below belong to apihub-api,
# which owns the identity-bridge endpoint POST /api/v1/auth/sso-token.
#
# Shared secret required in X-Bridge-Secret by that endpoint (app/api/v1/auth.py).
# Generate a fresh value per deployment; it is only ever exchanged
# docker-internally between apihub-mcp and apihub-api. MUST match the
# MCP_BRIDGE_SECRET you put in .env.mcp.
MCP_BRIDGE_SECRET=<openssl rand -base64 32>
# Auto-create a role-less local user on first SSO login via the bridge.
# Default false (site policy): unknown SSO users get 403 until an admin
# provisions them in the Admin UI instead of being silently auto-created.
MCP_BRIDGE_AUTOCREATE=false
```

- [ ] **Step 2: Verify no MCP-server-only vars remain in the main example**

```bash
grep -nE 'MCP_PUBLIC_URL|MCP_AUTH_ISSUER|MCP_INTROSPECTION|OAUTH_INTROSPECTION_SECRET' deploy/apihub/.env.example
```
Expected: no output. Then confirm the two kept vars are present:

```bash
grep -nE 'MCP_BRIDGE_SECRET|MCP_BRIDGE_AUTOCREATE' deploy/apihub/.env.example
```
Expected: both present.

- [ ] **Step 3: Commit**

```bash
git add deploy/apihub/.env.example
git commit -m "refactor(deploy): move MCP-server env vars out of main .env.example"
```

---

### Task 5: Update prod `deploy/apihub/README.md`

**Files:**
- Modify: `deploy/apihub/README.md`

**Interfaces:**
- Produces: README describing the two-file (`.env` + `.env.mcp`) workflow and the now-auto-registered MCP app route.

- [ ] **Step 1: Update the Deploy + MCP sections**

Apply these edits to `deploy/apihub/README.md`:

1. In the **Deploy** fenced block, change `cd deploy/isap` → `cd deploy/apihub`.

2. Replace the **MCP server** intro paragraph (currently "`docker-compose.yml` also brings up `apihub-mcp` …") with:

```markdown
The MCP server is an **optional add-on** in a separate compose file,
`docker-compose.mcp.yml` (env: `.env.mcp`). Bring it up after the main stack:

    cd deploy/apihub
    cp .env.mcp.example .env.mcp   # edit every <...> value; MCP_BRIDGE_SECRET must match .env
    grep -nE '<[^>]+>' .env.mcp    # must return nothing
    docker compose -f docker-compose.mcp.yml up -d

It brings up `apihub-mcp` — the Streamable HTTP MCP transport
(`mcp_server/http_app.py`, port 8810 internal), a thin proxy over `apihub-api`'s
REST API. It shares the built `oracle-api-hub:latest` image and `gateway-net`
only (no direct DB/Redis access). Phase 2 OAuth is enabled by default
(`MCP_OAUTH_ENABLED=true`): callers present a Bearer token from isap-auth, which
is introspected and exchanged for a local API Hub JWT via the identity-bridge
endpoint (`POST /api/v1/auth/sso-token`, gated by `settings.mcp_bridge_secret`).
That bridge path is blocked at the gateway (`gateway-conf/apihub.conf` returns a
hard 404 for `/apihub/api/v1/auth/sso-token`) — only the docker-internal
`apihub-mcp` -> `apihub-api:8000` call can ever reach it.
```

3. Replace the **Gateway wiring (manual — outside this repo)** section so step 1
(the app-scoped route) is now automatic:

```markdown
### Gateway wiring

The **app-scoped MCP route** (`gateway-conf/apihub-mcp.conf`) is now
auto-registered by the `apihub-mcp-route-register` one-shot in
`docker-compose.mcp.yml` (same mechanism as `apihub-route-register`) — no manual
copy needed.

The **root well-known rewrite** is still manual, because it is a shared,
multi-app file this repo must not overwrite:

1. `gateway-conf/oauth-wellknown-apihub-mcp.snippet.conf` holds the
   `location ^~ /.well-known/oauth-protected-resource/apihub/mcp` block. Paste it
   into the gateway's shared root well-known conf (the file playing the role of
   ITSM-PM's `oauth-wellknown.conf`) alongside the other apps' entries — do not
   replace that file's existing content.
2. Reload: `docker exec isap-gateway nginx -t && docker exec isap-gateway nginx -s reload`

If this gateway does not yet ship `conf.d/snippets/internal-only.conf` (the
RFC1918 allowlist both new confs `include`), create it from ITSM-PM's copy first.
```

4. In **Decommission**, update the manual-removal note and add the MCP stack teardown:

```markdown
    docker compose -f docker-compose.mcp.yml down   # if the MCP add-on was started
    docker compose down
    rm -f /opt/isap/gateway-apps/apihub.conf /opt/isap/gateway-apps/apihub-mcp.conf
    # The root well-known block was added manually — delete the apihub/mcp
    # location block from the shared root conf by hand.
    docker exec isap-gateway nginx -s reload
```

5. Any remaining literal `deploy/isap` strings in the file → `deploy/apihub`.

- [ ] **Step 2: Verify no stale references**

```bash
grep -nE 'deploy/isap|docker-compose\.yml also brings up' deploy/apihub/README.md
```
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add deploy/apihub/README.md
git commit -m "docs(deploy): update apihub README for MCP add-on + two-file env"
```

---

### Task 6: Scaffold `deploy/apihub-test/` — main stack

Create the test directory as a copy of prod, then overwrite the main-stack files with test values.

**Files:**
- Create: `deploy/apihub-test/docker-compose.yml`
- Create: `deploy/apihub-test/.env.example`
- Create: `deploy/apihub-test/db-init/create-database.sh` (identical to prod — engine-agnostic)
- Create: `deploy/apihub-test/sql_definitions/README.md` (identical to prod)

**Interfaces:**
- Produces: the test main stack. Containers `apihub-test-*`, `POSTGRES_DB=apihub_test`, route `/apihub-test`, Redis DBs `/3 /4 /5`. Task 7 adds its MCP add-on; Task 8 its gateway confs.

- [ ] **Step 1: Copy the prod dir as the starting point**

```bash
cd /c/app/Oracle-api-hub
cp -r deploy/apihub deploy/apihub-test
rm -f deploy/apihub-test/.env deploy/apihub-test/.env.mcp   # in case any local copies exist
```

- [ ] **Step 2: Overwrite `deploy/apihub-test/docker-compose.yml`**

```yaml
# ==============================================================================
# Oracle API Hub — ISAP platform deployment stack (TEST)
# ==============================================================================
# Test/UAT sibling of deploy/apihub/docker-compose.yml. Self-registers against
# the same shared ISAP infrastructure but with test-only names so it coexists
# with prod: container prefix apihub-test-*, DB apihub_test, route /apihub-test,
# Redis logical DBs 3/4/5 (prod uses 0/1/2).
#
# Deploy:  cd deploy/apihub-test && cp .env.example .env && <edit> && docker compose up -d --build
# ==============================================================================

services:
  # ── one-shot: create the platform-store database in shared postgres ───────
  apihub-test-db-init:
    image: postgres:16-alpine
    container_name: apihub-test-db-init
    restart: "no"
    env_file: [.env]
    networks: [db-net]
    entrypoint: ["sh", "/db-init/create-database.sh"]
    volumes:
      - ./db-init:/db-init:ro

  # ── one-shot: create tables + seed default admin (idempotent) ─────────────
  apihub-test-migrate:
    build:
      context: ../..
    image: oracle-api-hub:latest
    container_name: apihub-test-migrate
    restart: "no"
    env_file: [.env]
    networks: [db-net, redis-net]
    command: ["python", "-m", "scripts.init_db"]
    depends_on:
      apihub-test-db-init:
        condition: service_completed_successfully

  # ── one-shot: register the gateway route and reload nginx ─────────────────
  apihub-test-route-register:
    image: docker:27-cli
    container_name: apihub-test-route-register
    restart: "no"
    volumes:
      - ./gateway-conf/apihub-test.conf:/conf/apihub-test.conf:ro
      - ${INFRA_GATEWAY_APPS_DIR:-/opt/isap/gateway-apps}:/dest
      - /var/run/docker.sock:/var/run/docker.sock
    # See deploy/apihub/docker-compose.yml for the inlined-script contract
    # ($$ escapes, whole script in entrypoint list).
    entrypoint:
      - sh
      - -c
      - |
        set -e
        DEST=/dest/apihub-test.conf
        BAK=/dest/.apihub-test.conf.bak
        [ -f "$$DEST" ] && cp -f "$$DEST" "$$BAK" || true
        cp -f /conf/apihub-test.conf "$$DEST"
        if docker exec isap-gateway nginx -t; then
          if docker kill -s HUP isap-gateway; then
            echo "apihub-test route registered and gateway reloaded."
            rm -f "$$BAK"
          else
            echo "route conf valid but SIGHUP reload of isap-gateway failed." >&2
            exit 1
          fi
        else
          echo "nginx -t failed; restoring previous conf to avoid breaking the gateway." >&2
          if [ -f "$$BAK" ]; then mv -f "$$BAK" "$$DEST"; else rm -f "$$DEST"; fi
          exit 1
        fi

  # ── long-running: FastAPI app ─────────────────────────────────────────────
  apihub-test-api:
    build:
      context: ../..
    image: oracle-api-hub:latest
    container_name: apihub-test-api
    restart: unless-stopped
    env_file: [.env]
    environment:
      MCP_BRIDGE_SECRET: ${MCP_BRIDGE_SECRET:-}
      MCP_BRIDGE_AUTOCREATE: ${MCP_BRIDGE_AUTOCREATE:-false}
    networks: [gateway-net, db-net, redis-net]
    volumes:
      - ../../app/database_definitions:/app/app/database_definitions
      - ./sql_definitions:/app/app/sql_definitions
      - ../../app/admin:/app/app/admin:ro
    depends_on:
      apihub-test-migrate:
        condition: service_completed_successfully
      apihub-test-route-register:
        condition: service_completed_successfully

  # ── long-running: Celery write-queue worker ───────────────────────────────
  apihub-test-celery-write:
    image: oracle-api-hub:latest
    container_name: apihub-test-celery-write
    restart: unless-stopped
    env_file: [.env]
    networks: [db-net, redis-net]
    volumes:
      - ../../app/database_definitions:/app/app/database_definitions
      - ./sql_definitions:/app/app/sql_definitions
    command: ["celery", "-A", "app.tasks.celery_app", "worker", "-Q", "write", "-l", "info", "--concurrency=4"]
    depends_on:
      apihub-test-api:
        condition: service_started

  # ── long-running: Celery procedure-queue worker ───────────────────────────
  apihub-test-celery-proc:
    image: oracle-api-hub:latest
    container_name: apihub-test-celery-proc
    restart: unless-stopped
    env_file: [.env]
    networks: [db-net, redis-net]
    volumes:
      - ../../app/database_definitions:/app/app/database_definitions
      - ./sql_definitions:/app/app/sql_definitions
    command: ["celery", "-A", "app.tasks.celery_app", "worker", "-Q", "procedure", "-l", "info", "--concurrency=2"]
    depends_on:
      apihub-test-api:
        condition: service_started

networks:
  gateway-net: { external: true }
  db-net:      { external: true }
  redis-net:   { external: true }
```

- [ ] **Step 3: Overwrite `deploy/apihub-test/.env.example`**

```bash
# ==============================================================================
# Oracle API Hub — ISAP deployment environment (TEST)
#   cp .env.example .env  &&  edit every <...> value
# ==============================================================================
# Test/UAT sibling of deploy/apihub/.env.example. Differs from prod in every
# value that touches shared infra so the two coexist.

# --- Reverse-proxy prefix (MUST match the gateway route /apihub-test) ---
ROOT_PATH=/apihub-test

# --- Platform store: shared isap-postgres (separate DB from prod!) -------------
POSTGRES_HOST=isap-postgres
POSTGRES_PORT=5432
POSTGRES_DB=apihub_test
POSTGRES_USER=isap_admin
POSTGRES_PASSWORD=<shared infra PG_ADMIN_PASSWORD>

# --- Celery / Redis: shared isap-redis ----------------------------------------
# IMPORTANT: test uses logical DBs 3/4/5 so its Celery broker/queues are isolated
# from prod (0/1/2). Reusing prod's broker DB would let a prod worker execute a
# test task (shared queue names 'write'/'procedure') and vice-versa.
REDIS_URL=redis://default:<REDIS_PASSWORD>@isap-redis:6379/3
CELERY_BROKER_URL=redis://default:<REDIS_PASSWORD>@isap-redis:6379/4
CELERY_RESULT_BACKEND=redis://default:<REDIS_PASSWORD>@isap-redis:6379/5

# --- JWT ----------------------------------------------------------------------
# Use a DIFFERENT secret from production.
SECRET_KEY=<openssl rand -base64 48 — different from prod>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# --- Data sources / targets ---------------------------------------------------
# Connections are defined in app/database_definitions/databases.yaml (mounted).
# The YAML holds NO plaintext passwords; supply each one here as
# DB_{ALIAS_UPPER}_PASSWORD. One line per alias in databases.yaml.
DB_DEFAULT_PASSWORD=<oracle "default" alias password>
DB_PG_TEST_PASSWORD=<postgres "pg_test" alias password>

# Oracle thick-mode client dir (leave empty — bundled in the image via ldconfig)
ORACLE_CLIENT_DIR=

# --- Route-register: host dir the gateway includes confs from -----------------
INFRA_GATEWAY_APPS_DIR=/opt/isap/gateway-apps

# --- MCP identity bridge (apihub-test-api side) -------------------------------
# The MCP server has its own env file (.env.mcp / .env.mcp.example) used by
# docker-compose.mcp.yml. Only these two bridge vars belong to apihub-test-api.
# MCP_BRIDGE_SECRET MUST match the value you put in .env.mcp.
MCP_BRIDGE_SECRET=<openssl rand -base64 32>
MCP_BRIDGE_AUTOCREATE=false

# --- API ----------------------------------------------------------------------
API_TITLE=Oracle API Hub (TEST)
API_VERSION=1.0.0
API_PREFIX=/api/v1
DEBUG=false
RATE_LIMIT_PER_MINUTE=60
SQL_DEFINITIONS_DIR=app/sql_definitions
```

- [ ] **Step 4: Confirm db-init and sql_definitions carried over unchanged**

```bash
diff deploy/apihub/db-init/create-database.sh deploy/apihub-test/db-init/create-database.sh && echo "db-init identical OK"
diff deploy/apihub/sql_definitions/README.md deploy/apihub-test/sql_definitions/README.md && echo "sqldef readme identical OK"
```
Expected: both print the `... OK` line (no diff). These files are env-agnostic and correctly identical.

- [ ] **Step 5: Verify the test main compose parses + differs from prod**

```bash
cd deploy/apihub-test && cp .env.example .env
docker compose config -q && echo "test main compose OK"
docker compose config | grep -c 'container_name: apihub-test-'   # expect 6
rm -f .env && cd /c/app/Oracle-api-hub
```
Expected: `test main compose OK`; count `6` (db-init, migrate, route-register, api, celery-write, celery-proc).

- [ ] **Step 6: Commit**

```bash
git add deploy/apihub-test/docker-compose.yml deploy/apihub-test/.env.example \
        deploy/apihub-test/db-init deploy/apihub-test/sql_definitions
git commit -m "feat(deploy): scaffold apihub-test main stack"
```

---

### Task 7: `deploy/apihub-test/` — MCP add-on

**Files:**
- Create: `deploy/apihub-test/docker-compose.mcp.yml`
- Create: `deploy/apihub-test/.env.mcp.example`

**Interfaces:**
- Consumes: `oracle-api-hub:latest`, `apihub-test-api` (from test main compose), `gateway-net`.
- Produces: `apihub-test-mcp` (8810) + `apihub-test-mcp-route-register` registering `apihub-test-mcp.conf`.

- [ ] **Step 1: Overwrite `deploy/apihub-test/docker-compose.mcp.yml`**

```yaml
# ==============================================================================
# Oracle API Hub — MCP server add-on (TEST)
# ==============================================================================
# Test/UAT sibling of deploy/apihub/docker-compose.mcp.yml. Registers the MCP
# route /apihub-test/mcp/* and runs apihub-test-mcp. Bring up the test main
# stack first (it builds oracle-api-hub:latest and runs apihub-test-api).
#
# Deploy:
#   cd deploy/apihub-test && cp .env.mcp.example .env.mcp   # edit every <...> value
#   docker compose -f docker-compose.mcp.yml up -d
# ==============================================================================

services:
  apihub-test-mcp-route-register:
    image: docker:27-cli
    container_name: apihub-test-mcp-route-register
    restart: "no"
    volumes:
      - ./gateway-conf/apihub-test-mcp.conf:/conf/apihub-test-mcp.conf:ro
      - ${INFRA_GATEWAY_APPS_DIR:-/opt/isap/gateway-apps}:/dest
      - /var/run/docker.sock:/var/run/docker.sock
    entrypoint:
      - sh
      - -c
      - |
        set -e
        DEST=/dest/apihub-test-mcp.conf
        BAK=/dest/.apihub-test-mcp.conf.bak
        [ -f "$$DEST" ] && cp -f "$$DEST" "$$BAK" || true
        cp -f /conf/apihub-test-mcp.conf "$$DEST"
        if docker exec isap-gateway nginx -t; then
          if docker kill -s HUP isap-gateway; then
            echo "apihub-test-mcp route registered and gateway reloaded."
            rm -f "$$BAK"
          else
            echo "route conf valid but SIGHUP reload of isap-gateway failed." >&2
            exit 1
          fi
        else
          echo "nginx -t failed; restoring previous conf to avoid breaking the gateway." >&2
          if [ -f "$$BAK" ]; then mv -f "$$BAK" "$$DEST"; else rm -f "$$DEST"; fi
          exit 1
        fi

  apihub-test-mcp:
    image: oracle-api-hub:latest
    container_name: apihub-test-mcp
    restart: unless-stopped
    env_file: [.env.mcp]
    command: ["uvicorn", "mcp_server.http_app:app", "--host", "0.0.0.0", "--port", "8810"]
    networks: [gateway-net]
    depends_on:
      apihub-test-mcp-route-register:
        condition: service_completed_successfully

networks:
  gateway-net: { external: true }
```

- [ ] **Step 2: Overwrite `deploy/apihub-test/.env.mcp.example`**

```bash
# ==============================================================================
# Oracle API Hub — MCP server (docker-compose.mcp.yml) environment (TEST)
#   cp .env.mcp.example .env.mcp  &&  edit every <...> value
# ==============================================================================
# Consumed only by apihub-test-mcp. MCP_BRIDGE_SECRET here MUST equal the
# MCP_BRIDGE_SECRET in .env.

MCP_API_HUB_URL=http://apihub-test-api:8000

MCP_OAUTH_ENABLED=true
MCP_PUBLIC_URL=https://<GATEWAY_SERVER_NAME>/apihub-test/mcp
MCP_AUTH_ISSUER=https://<GATEWAY_SERVER_NAME>/auth
MCP_INTROSPECTION_URL=http://isap-auth:9997/auth/api/v1/introspect
MCP_INTROSPECTION_SECRET=<same value as isap-auth's OAUTH_INTROSPECTION_SECRET>
MCP_BRIDGE_SECRET=<same openssl value as .env MCP_BRIDGE_SECRET>
```

- [ ] **Step 3: Verify the test MCP compose parses**

```bash
cd deploy/apihub-test && cp .env.mcp.example .env.mcp
docker compose -f docker-compose.mcp.yml config -q && echo "test mcp compose OK"
docker compose -f docker-compose.mcp.yml config | grep -E 'container_name: apihub-test-mcp'   # 2 services
rm -f .env.mcp && cd /c/app/Oracle-api-hub
```
Expected: `test mcp compose OK`; both `apihub-test-mcp` and `apihub-test-mcp-route-register` present.

- [ ] **Step 4: Commit**

```bash
git add deploy/apihub-test/docker-compose.mcp.yml deploy/apihub-test/.env.mcp.example
git commit -m "feat(deploy): add apihub-test MCP add-on"
```

---

### Task 8: `deploy/apihub-test/` — gateway confs

Replace the three prod-named confs (copied in Task 6) with test-named, test-routed versions.

**Files:**
- Create: `deploy/apihub-test/gateway-conf/apihub-test.conf`
- Create: `deploy/apihub-test/gateway-conf/apihub-test-mcp.conf`
- Create: `deploy/apihub-test/gateway-conf/oauth-wellknown-apihub-test-mcp.snippet.conf`
- Delete: the copied `apihub.conf`, `apihub-mcp.conf`, `oauth-wellknown-apihub-mcp.snippet.conf` under `deploy/apihub-test/gateway-conf/`

**Interfaces:**
- Produces: gateway routes `/apihub-test/`, `/apihub-test/mcp/`, and well-known `/.well-known/oauth-protected-resource/apihub-test/mcp`, all upstreaming to `apihub-test-*` containers.

- [ ] **Step 1: Remove the prod-named confs that were copied in**

```bash
cd /c/app/Oracle-api-hub
rm -f deploy/apihub-test/gateway-conf/apihub.conf \
      deploy/apihub-test/gateway-conf/apihub-mcp.conf \
      deploy/apihub-test/gateway-conf/oauth-wellknown-apihub-mcp.snippet.conf
```

- [ ] **Step 2: Create `deploy/apihub-test/gateway-conf/apihub-test.conf`**

```nginx
# Oracle API Hub (TEST) — registered route on the ISAP gateway.
# Bare location blocks only (included inside the gateway's server context).
# Uses `set $apihub_test_up` lazy resolution so nginx starts even when
# apihub-test-api is not yet running (per CONTRACT.md).
#
# IMPORTANT: do NOT strip the /apihub-test prefix here. The app runs with
# root_path=/apihub-test and strips the prefix itself. If nginx also strips it,
# StaticFiles mounts (/admin/css, /admin/js) 404. Forward the full URI.

location = /apihub-test {
    return 301 /apihub-test/;
}

# MCP identity bridge — internal only; must never be reachable via the gateway.
location = /apihub-test/api/v1/auth/sso-token {
    deny all;
    return 404;
}

location = /apihub-test/api/v1/auth/sso-token/ {
    deny all;
    return 404;
}

location /apihub-test/ {
    set $apihub_test_up http://apihub-test-api:8000;
    # Forward the original URI unchanged (incl. /apihub-test); root_path handles it.
    proxy_pass $apihub_test_up$request_uri;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /apihub-test;

    proxy_read_timeout 300s;
    proxy_connect_timeout 10s;

    # YAML-definition import upload
    client_max_body_size 50m;
}
```

- [ ] **Step 3: Create `deploy/apihub-test/gateway-conf/apihub-test-mcp.conf`**

```nginx
# Oracle API Hub (TEST) MCP Server routing
# Proxies /apihub-test/mcp/* -> apihub-test-mcp:8810/*
#
# IMPORTANT: the upstream Streamable HTTP endpoint is mounted at /mcp/ (with a
# trailing slash) — see mcp_server/http_app.py `Mount("/mcp", ...)`. Register
# clients against the trailing-slash form:
#   https://<gateway>/apihub-test/mcp/mcp/
#
# Auto-registered by apihub-test-mcp-route-register (docker-compose.mcp.yml).

location /apihub-test/mcp/ {
    # Restrict to internal network only (RFC1918).
    include /etc/nginx/conf.d/snippets/internal-only.conf;

    set $upstream_apihub_test_mcp http://apihub-test-mcp:8810;

    # Strip /apihub-test/mcp prefix: rewrite URI before proxy.
    rewrite ^/apihub-test/mcp(/.*) $1 break;
    proxy_pass $upstream_apihub_test_mcp;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Streamable HTTP / SSE-specific settings — long-lived connections.
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
    chunked_transfer_encoding on;

    # Disable gzip for event-stream responses.
    proxy_set_header Accept-Encoding "";
}
```

- [ ] **Step 4: Create `deploy/apihub-test/gateway-conf/oauth-wellknown-apihub-test-mcp.snippet.conf`**

```nginx
# OAuth protected-resource well-known rewrite for the Oracle API Hub (TEST) MCP
# server — RFC 9728 "insertion form" metadata document.
#
# WHERE THIS GOES: NOT an app-scoped conf. RFC 9728 clients insert
# /.well-known/oauth-protected-resource at the DOMAIN ROOT:
#   https://<gateway>/apihub-test/mcp  (resource identifier)
#     -> https://<gateway>/.well-known/oauth-protected-resource/apihub-test/mcp
# This block MUST be added to the gateway's shared root well-known conf by a
# platform operator (alongside apihub, itpm, etc.) — this repo cannot
# self-register it without clobbering other apps' entries. See README.md.

location ^~ /.well-known/oauth-protected-resource/apihub-test/mcp {
    include /etc/nginx/conf.d/snippets/internal-only.conf;
    set $upstream_apihub_test_mcp_wellknown http://apihub-test-mcp:8810;
    rewrite ^ /.well-known/oauth-protected-resource break;
    proxy_pass $upstream_apihub_test_mcp_wellknown;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

- [ ] **Step 5: Verify the conf set is test-only and internally consistent**

```bash
ls deploy/apihub-test/gateway-conf
# expect exactly: apihub-test.conf  apihub-test-mcp.conf  oauth-wellknown-apihub-test-mcp.snippet.conf
grep -rnE 'apihub-api|apihub-mcp:8810|\$apihub_up|/apihub/' deploy/apihub-test/gateway-conf
# expect: no output (no prod hostnames/vars/paths leaked into test confs)
grep -rn 'apihub-test-api:8000\|apihub-test-mcp:8810' deploy/apihub-test/gateway-conf | wc -l
# expect: 3 (api route, mcp route, well-known)
```
Expected: only the three test files; no prod leakage; 3 test upstream references.

- [ ] **Step 6: Commit**

```bash
git add deploy/apihub-test/gateway-conf
git commit -m "feat(deploy): add apihub-test gateway confs (/apihub-test routes)"
```

---

### Task 9: Update `deploy/apihub-test/README.md` for the test env

**Files:**
- Modify: `deploy/apihub-test/README.md` (copied from prod in Task 6)

**Interfaces:**
- Produces: a README describing the test stack (paths, `apihub-test`, `/apihub-test/`).

- [ ] **Step 1: Retitle and re-path the README**

Edit `deploy/apihub-test/README.md`:
1. Title → `# Deploying Oracle API Hub (TEST) to the ISAP Gateway`.
2. Reachable-at line → `https://<GATEWAY_SERVER_NAME>/apihub-test/`.
3. In every command/path, `deploy/apihub` → `deploy/apihub-test`, `/apihub/` → `/apihub-test/`, `apihub.conf` → `apihub-test.conf`, `apihub-mcp.conf` → `apihub-test-mcp.conf`, `apihub-api`/`apihub-mcp` → `apihub-test-api`/`apihub-test-mcp`, and the container-status names (`apihub-db-init` etc.) → `apihub-test-*`.
4. Add a note under the title: `> Test/UAT sibling of deploy/apihub. Uses DB apihub_test and Redis logical DBs 3/4/5 so it coexists with prod on the shared infra.`

- [ ] **Step 2: Verify no prod-only strings remain**

```bash
grep -nE '/apihub/|apihub\.conf|apihub-mcp\.conf|apihub-api:|apihub-mcp:|deploy/apihub[^-]' deploy/apihub-test/README.md
```
Expected: no output (every reference is the `-test` variant).

- [ ] **Step 3: Commit**

```bash
git add deploy/apihub-test/README.md
git commit -m "docs(deploy): apihub-test README"
```

---

### Task 10: Update living docs (`CLAUDE.md`, `README.md`)

**Files:**
- Modify: `CLAUDE.md:94`
- Modify: `README.md:272-278`

**Interfaces:**
- Produces: repo-level docs that point at `deploy/apihub/` and mention `deploy/apihub-test/`.

- [ ] **Step 1: Update `CLAUDE.md`**

Replace `deploy/isap/docker-compose.yml` (ISAP gateway)` with `deploy/apihub/docker-compose.yml` (ISAP gateway; `deploy/apihub-test/` for the test/UAT stack)` in the bind-mount bullet (line 94).

- [ ] **Step 2: Update `README.md` "Deploying to the ISAP platform gateway"**

Replace the paragraph body so it reads:

```markdown
To run behind the shared ISAP nginx gateway at `/apihub/`, use the
self-registering stack in [`deploy/apihub/`](deploy/apihub/README.md). It creates
its DB in `isap-postgres`, uses `isap-redis` for Celery, and drops its own
nginx route — no edits to the `isap-infrastructure` repo. Set `ROOT_PATH=/apihub`
so the app and admin UI generate prefix-aware URLs. A parallel test/UAT stack
lives in [`deploy/apihub-test/`](deploy/apihub-test/README.md) (route
`/apihub-test/`, DB `apihub_test`, Redis logical DBs 3/4/5).
```

- [ ] **Step 3: Verify no stray `deploy/isap` references remain in living docs**

```bash
grep -rnE 'deploy/isap|deploy\\isap' CLAUDE.md README.md
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: point living docs at deploy/apihub and deploy/apihub-test"
```

---

### Task 11: Final static verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: All four compose files parse**

```bash
cd /c/app/Oracle-api-hub/deploy/apihub      && cp .env.example .env && cp .env.mcp.example .env.mcp
docker compose config -q && docker compose -f docker-compose.mcp.yml config -q && echo "PROD OK"
rm -f .env .env.mcp
cd /c/app/Oracle-api-hub/deploy/apihub-test && cp .env.example .env && cp .env.mcp.example .env.mcp
docker compose config -q && docker compose -f docker-compose.mcp.yml config -q && echo "TEST OK"
rm -f .env .env.mcp
cd /c/app/Oracle-api-hub
```
Expected: `PROD OK` and `TEST OK`.

- [ ] **Step 2: No `deploy/isap` references anywhere except historical docs**

```bash
grep -rnE 'deploy/isap|deploy\\isap' --include='*.md' --include='*.yml' --include='*.yaml' . \
  | grep -v 'docs/superpowers/'
```
Expected: no output (only dated `docs/superpowers/**` may still mention it, by design).

- [ ] **Step 3: Prod vs test share nothing that collides on shared infra**

```bash
# Container names disjoint
diff <(grep -rho 'container_name: [a-z0-9-]*' deploy/apihub/*.yml | sort -u) \
     <(grep -rho 'container_name: [a-z0-9-]*' deploy/apihub-test/*.yml | sort -u)
# expect: every line differs (apihub-* vs apihub-test-*)

# DB + Redis isolation present in test
grep -nE 'POSTGRES_DB=apihub_test|6379/3|6379/4|6379/5' deploy/apihub-test/.env.example   # 4 hits
grep -nE 'POSTGRES_DB=apihub$|6379/0|6379/1|6379/2'      deploy/apihub/.env.example        # 4 hits
```
Expected: container-name sets fully disjoint; test uses `apihub_test` + Redis 3/4/5; prod uses `apihub` + Redis 0/1/2.

- [ ] **Step 4: Final commit (if any verification touched tracked files — normally none)**

```bash
git status --short   # expect clean
```

---

## Notes for the implementer

- `.env` / `.env.mcp` are git-ignored; the temp copies created for `docker compose config` checks are removed in the same step. If `docker compose config` complains about a missing external network, that is expected off-host — the parse still validates the file; use `docker compose config -q` which only fails on *syntax/interpolation* errors, not on absent external networks.
- The `oracle-api-hub:latest` image is shared by both stacks intentionally (same working tree). Test containers differ only by name/config/data, never code.
- Running the test stack `up` will rebuild and retag `oracle-api-hub:latest`; this does not disturb already-created prod containers (they run off the image they were created from).
