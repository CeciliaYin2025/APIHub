# Oracle API Hub → ISAP Gateway Deployment — Design

**Date:** 2026-06-09
**Status:** Approved

## Goal

Deploy the Oracle API Hub onto the existing **ISAP platform infrastructure**
(`C:\app\isap-infrastructure`) so it is reachable through the shared nginx
gateway at `https://isap.sercomm.com/apihub/`, using the platform's shared
PostgreSQL and Redis. Done by **self-registration** — the app's own compose
stack drops a route conf and creates its own DB. **The `isap-infrastructure`
repo is never edited** (its cardinal rule).

## Context (from the infra `CONTRACT.md` v1.1)

- External docker bridges: `gateway-net`, `db-net`, `redis-net`.
- Shared Postgres: host `isap-postgres:5432`, admin user `isap_admin` (v1: all
  apps share the admin user; isolate by unique `DB_NAME`).
- Shared Redis: host `isap-redis:6379`, single `requirepass` (`REDIS_PASSWORD`),
  key isolation by prefix/convention only.
- Gateway includes any `*.conf` in `${INFRA_GATEWAY_APPS_DIR}` (default
  `/opt/isap/gateway-apps`). Conf files contain bare `location` blocks (no
  `server`/`http` wrapper). Must use `set $upstream http://...;` lazy resolution
  so nginx starts even when the app container is down.
- Apps self-register via two one-shot init containers: `<app>-db-init` (creates
  its DB) and `<app>-route-register` (copies conf + `SIGHUP isap-gateway`).

## Decisions

| Decision | Choice |
|---|---|
| Backing services | Shared `isap-postgres` (platform store) + shared `isap-redis` (Celery) |
| URL prefix | `/apihub/` |
| Deploy target | Linux host (the real ISAP host; the Windows checkout is for authoring) |
| App code changes | Yes — small, backward-compatible `ROOT_PATH` support |
| Flower | Omitted from gateway deployment (no built-in auth); left commented for later |

## Topology

Reachable surface:
- `/apihub/admin` → admin SPA
- `/apihub/api/v1/...` → REST API
- `/apihub/docs`, `/apihub/openapi.json` → Swagger

| Container | Networks | Role |
|---|---|---|
| `apihub-db-init` (one-shot) | db-net | create DB `apihub` in `isap-postgres` (idempotent) |
| `apihub-migrate` (one-shot) | db-net, redis-net | run `python -m scripts.init_db` (tables + admin seed; idempotent) |
| `apihub-route-register` (one-shot) | — | copy `apihub.conf` to gateway-apps dir, `nginx -t`, `SIGHUP isap-gateway` |
| `apihub-api` | gateway-net, db-net, redis-net | uvicorn `app.main:app` :8000 |
| `apihub-celery-write` | db-net, redis-net | Celery `write` queue worker |
| `apihub-celery-proc` | db-net, redis-net | Celery `procedure` queue worker |

Data sources (Oracle / external PostgreSQL / MSSQL) are unchanged — reached via
normal container egress, not the ISAP bridges.

Backing-service wiring (deploy `.env`):
- `POSTGRES_HOST=isap-postgres`, `POSTGRES_PORT=5432`, `POSTGRES_DB=apihub`,
  `POSTGRES_USER=isap_admin`, `POSTGRES_PASSWORD=<shared infra pw>`
- `REDIS_URL=redis://default:<pw>@isap-redis:6379/0`
- `CELERY_BROKER_URL=redis://default:<pw>@isap-redis:6379/1`
- `CELERY_RESULT_BACKEND=redis://default:<pw>@isap-redis:6379/2`
- `ROOT_PATH=/apihub`, fresh `SECRET_KEY`

## Path-prefix handling (the one part needing app changes)

nginx **strips** the `/apihub` prefix, so the app stays internally `/admin` +
`/api/v1`. But the admin UI hard-codes **absolute** URLs (`/admin/css/...`,
`/admin/js/app.js`, `const API = '/api/v1'`) which a browser resolves against
the origin root → 404 behind the prefix. Fix: make the app base-path aware via a
new `ROOT_PATH` env var, **defaulting to empty so standalone behavior is
unchanged**.

App changes:
- `app/core/config.py`: add `root_path: str = ""`.
- `app/main.py`: pass `root_path=settings.root_path` to `FastAPI(...)` (fixes
  Swagger/OpenAPI under the prefix); when serving `index.html`, inject the base
  path (e.g. a `window.__BASE_PATH__` script tag) so the SPA knows its prefix.
- `app/admin/index.html` + `app/admin/js/app.js`: derive asset URLs and the
  `API` base from `window.__BASE_PATH__` (falling back to `''`) instead of
  literal `/admin` / `/api/v1`.

nginx conf (`deploy/isap/gateway-conf/apihub.conf`):
```nginx
location = /apihub { return 301 /apihub/; }
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
    client_max_body_size 50m;   # YAML-import upload
}
```

**Alternative considered & rejected:** nginx `sub_filter` rewriting absolute
paths in responses (no app code) — fragile: must enumerate every literal,
breaks under gzip, easy to miss one. `ROOT_PATH` is small, explicit, and makes
the app a proper path-routing citizen.

## New files (all in the Oracle API Hub repo)

```
deploy/isap/
├── docker-compose.yml        # self-registering stack (build context = repo root)
├── .env.example              # shared-infra creds + app secrets
├── db-init/create-database.sh
├── gateway-conf/apihub.conf
└── README.md                 # Linux-host deploy steps
```

`apihub-api` depends on `apihub-db-init` and `apihub-migrate`
(`service_completed_successfully`) and `apihub-route-register`. `apihub-migrate`
depends on `apihub-db-init`. Image built from the existing root `Dockerfile`.

## Known risks (flagged, not fixed here)

- **Dockerfile Oracle Instant Client mismatch**: downloads the *latest*
  `basiclite` zip but hard-codes `LD_LIBRARY_PATH=.../instantclient_21_13`. If
  the unzip dir name differs, Oracle **thick mode** (needed for 11g) fails; thin
  mode (12.1+) is unaffected. Separate concern.
- **Shared Redis**: Celery uses logical DBs 0/1/2 on `isap-redis`; isolation is
  by convention only (per CONTRACT). Acceptable for v1.

## Out of scope

- Editing `isap-infrastructure`.
- Per-app Postgres roles / Redis ACLs (infra v2).
- Fixing the Instant Client version pin.
- Flower exposure (left commented).
