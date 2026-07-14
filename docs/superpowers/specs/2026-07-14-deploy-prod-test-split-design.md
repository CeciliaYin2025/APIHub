# Deploy: prod/test split + rename `isap` → `apihub`

**Date:** 2026-07-14
**Status:** Approved (design)
**Reference pattern:** `../e-Budget Management Platform/deploy` (`ebudget/` + `ebudget-test/`)

## Goal

Restructure the single `deploy/isap/` deployment into **two parallel, self-contained
deployments** — production (`apihub`) and test (`apihub-test`) — mirroring the
`e-Budget Management Platform` convention, and rename the `isap` directory to
`apihub`. Both stacks self-register against the same shared ISAP infrastructure
(`isap-postgres`, `isap-redis`, `isap-gateway`) and coexist without conflict.

Non-goals: no application-code changes; no changes to local-dev `docker-compose.yaml`;
historical `docs/superpowers/specs|plans/*` are left untouched.

## Directory layout

```
deploy/
  apihub/                       # PROD — git mv from isap/
    docker-compose.yml          # db-init, migrate, route-register, api, celery-write, celery-proc
    docker-compose.mcp.yml      # NEW — MCP add-on: mcp-route-register + mcp
    .env.example                # main stack vars (keeps MCP_BRIDGE_SECRET for the api bridge)
    .env.mcp.example            # NEW — MCP-server-only vars
    db-init/create-database.sh
    gateway-conf/apihub.conf
    gateway-conf/apihub-mcp.conf
    gateway-conf/oauth-wellknown-apihub-mcp.snippet.conf
    sql_definitions/README.md
    README.md
  apihub-test/                  # TEST — NEW, parallel copy with test values
    docker-compose.yml
    docker-compose.mcp.yml
    .env.example
    .env.mcp.example
    db-init/create-database.sh
    gateway-conf/apihub-test.conf
    gateway-conf/apihub-test-mcp.conf
    gateway-conf/oauth-wellknown-apihub-test-mcp.snippet.conf
    sql_definitions/README.md
    README.md
```

Each directory is fully self-contained (the current `isap` style — no shared
deploy-root scripts). This differs from e-Budget only in that e-Budget keeps some
shared root files; apihub already inlines its route-register logic, so no shared
scripts are needed.

## MCP split (both envs)

Currently MCP is bundled inline in the single `docker-compose.yml`. It moves into a
separate `docker-compose.mcp.yml` add-on (mirrors e-Budget), and the MCP gateway
route gains an **auto-register one-shot** (`*-mcp-route-register`) instead of the
current manual-copy note.

- **`docker-compose.yml`** — `api` + `celery-write` + `celery-proc` + the three
  one-shots (`db-init`, `migrate`, `route-register`). `env_file: [.env]`. The `api`
  service keeps `MCP_BRIDGE_SECRET` / `MCP_BRIDGE_AUTOCREATE` because it owns the
  identity-bridge endpoint `POST /api/v1/auth/sso-token`.
- **`docker-compose.mcp.yml`** — `mcp` (long-running) + `mcp-route-register`
  (one-shot that drops `apihub-mcp.conf` into the gateway apps dir and SIGHUPs, using
  the same inlined script pattern as `route-register`). `env_file: [.env.mcp]`.
  Joins `gateway-net` only. `depends_on` the api being started (documented as a
  soft cross-stack dependency, since it lives in a separate compose file).
- **`.env.mcp.example`** — `MCP_OAUTH_ENABLED`, `MCP_PUBLIC_URL`, `MCP_AUTH_ISSUER`,
  `MCP_INTROSPECTION_URL`, `OAUTH_INTROSPECTION_SECRET`, `MCP_BRIDGE_SECRET`
  (**must match** the `.env` value — noted in comments), `MCP_API_HUB_URL`
  (prod `http://apihub-api:8000`; test **`http://apihub-test-api:8000`** — the
  env-specific api container).
- **`.env.example`** (main) drops the pure MCP-server vars but **keeps**
  `MCP_BRIDGE_SECRET` and `MCP_BRIDGE_AUTOCREATE`.

## prod → test value mapping

| Aspect | prod (`apihub`) | test (`apihub-test`) |
|---|---|---|
| Route / `ROOT_PATH` | `/apihub` | `/apihub-test` |
| Container prefix | `apihub-*` | `apihub-test-*` |
| Platform-store DB (`POSTGRES_DB`) | `apihub` | `apihub_test` |
| Gateway confs | `apihub.conf`, `apihub-mcp.conf` | `apihub-test.conf`, `apihub-test-mcp.conf` |
| well-known snippet path | `/.well-known/oauth-protected-resource/apihub/mcp` | `…/apihub-test/mcp` |
| `MCP_PUBLIC_URL` | `https://<gw>/apihub/mcp` | `https://<gw>/apihub-test/mcp` |
| `MCP_AUTH_ISSUER` | `https://<gw>/auth` | `https://<gw>/auth` (same AS) |
| `API_TITLE` | `Oracle API Hub` | `Oracle API Hub (TEST)` |
| `SECRET_KEY` | prod value | **different** value (comment says so) |
| Redis (isap-redis) | `REDIS_URL /0`, `CELERY_BROKER_URL /1`, `CELERY_RESULT_BACKEND /2` | `/3`, `/4`, `/5` |
| Image | `oracle-api-hub:latest` | `oracle-api-hub:latest` (same source tree; containers differ by name) |

### Why Redis DB isolation matters (test-specific)

Both stacks share `isap-redis`, and the Celery workers listen on hardcoded queue
names (`write`, `procedure`). If test reused the prod broker DB (`/1`), a task
enqueued by test could be executed by a **prod** worker (and vice-versa), since they
would share the same broker and queue names. Assigning test its own Redis DB indices
(`/3` cache, `/4` broker, `/5` backend) isolates the two Celery clusters. This is an
apihub-specific concern the e-Budget reference does not have.

## Gateway route coexistence

- Both stacks drop distinctly-named confs (`apihub.conf` vs `apihub-test.conf`, and
  the `-mcp` variants) into the same `${INFRA_GATEWAY_APPS_DIR}` — no filename
  collision, both routes live side by side.
- The internal-only bridge-blocking `location = /apihub/api/v1/auth/sso-token`
  (and trailing-slash variant) is duplicated in `apihub-test.conf` for
  `/apihub-test/api/v1/auth/sso-token`.
- The OAuth well-known snippet is a **shared, operator-owned** file on the gateway
  (not self-registered); each env ships its own snippet block for the operator to
  paste. Test uses the `/apihub-test/mcp` insertion path.

## Living-doc updates

- `git mv deploy/isap deploy/apihub` (preserves history).
- **`CLAUDE.md:94`** — `deploy/isap/docker-compose.yml` → `deploy/apihub/docker-compose.yml`.
- **`README.md:272-278`** — `deploy/isap/` → `deploy/apihub/`; add a sentence
  pointing at `deploy/apihub-test/` for the test/UAT stack.
- Each moved/created `README.md` updated to describe its own env, the MCP add-on
  compose, and the `.env` + `.env.mcp` two-file workflow.
- Leave dated `docs/superpowers/specs|plans/*` as historical record.

## Verification

Because there is no live ISAP infrastructure in this workspace, verification is
static:

1. `docker compose -f deploy/apihub/docker-compose.yml config` and the
   `docker-compose.mcp.yml`, plus the `apihub-test` equivalents, all parse
   (`config` succeeds) with a filled `.env` / `.env.mcp`.
2. Grep confirms no remaining `deploy/isap` references in living docs.
3. Cross-check that prod and test differ in exactly the mapped values (container
   names, DB name, route prefix, Redis DB indices, conf filenames) and share
   nothing that would collide on the shared infra.

## Out of scope

- e-Budget test extras (`sync-from-prod.sh`, `backups/`, UAT data-load runbooks).
- Any change to application code, `mcp_server/`, or local-dev `docker-compose.yaml`.
- Rewriting historical spec/plan documents.
