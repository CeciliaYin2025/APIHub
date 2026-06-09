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
curl -ksS https://<domain>/apihub/admin | grep '{{BASE_PATH}}'            # must print nothing
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
