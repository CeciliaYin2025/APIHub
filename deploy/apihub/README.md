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
cd deploy/apihub
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

## isap-auth prerequisites

Both MCP OAuth and Admin UI SSO depend on isap-auth. Two separate things must
be true before either works.

**1. Dynamic client registration must be enabled (MCP only).**

MCP clients (Claude Code and friends) have no pre-agreed `client_id`: they bind
a fresh ephemeral loopback port per attempt and register themselves through RFC
7591. That endpoint is `POST /auth/api/v1/register` on isap-auth, gated by
`OAUTH_DCR_ENABLED`, which **defaults to `False`**. With it off, client
registration answers `403 registration_disabled` and step 4 of the MCP
verification checklist below fails at the browser SSO stage.

This is a platform-level decision, not one this repo can make: the flag affects
every app on that authorization server, the endpoint takes no credential, and
isap-auth's `ci/policy-check.sh` greps to keep the default off. Ask the
isap-auth operators to enable it, and — per isap-auth README A.6 — to restrict
`/auth/api/v1/register` at the gateway to internal networks at the same time.

Admin UI SSO does **not** need this flag: it uses the bootstrap-token path
(`PUT /apps/{app_id}`), which is always available.

**2. This app must be registered (Admin UI SSO only).**

`apihub-app-register` does it automatically on `docker compose up` — see
`.env.isap.example`. Mint its token on the isap-auth host first:

    docker exec isap-auth python -m app.cli issue-token apihub --name "Oracle API Hub"

| Symptom | Cause |
|---|---|
| MCP client registration gets `403 registration_disabled` | `OAUTH_DCR_ENABLED` is off |
| `apihub-app-register` exits 1 with a 401 | bootstrap token invalid or revoked |
| `apihub-app-register` exits 1 with a 403 | token was issued for a different `app_id` |
| `/authorize` answers `Unknown app_id or redirect_uri not allowed` | `APIHUB_PUBLIC_URL` and `ISAP_REDIRECT_URIS` disagree |
| `apihub-app-register` prints "skipped" | no `ISAP_REGISTRATION_TOKEN` in `.env.isap` |

### Admin UI SSO verification (requires live environment)

1. `docker compose logs apihub-app-register` — shows `Registered 'apihub'` with
   the expected redirect_uri, exit 0.
2. Open `https://<GATEWAY_SERVER_NAME>/apihub/admin` — the login page shows
   "Sign in with ISAP SSO" above the password form.
3. Click it — the browser reaches isap-auth (already-logged-in users bounce
   straight back), returns to the Admin UI signed in, and the address bar has
   **no** `#sso_token=` left in it.
4. The user badge shows the SSO username, not a shared service account.
5. With `SSO_AUTOCREATE=false`, an SSO user with no API Hub account lands back
   on the login page with "Ask an administrator to create one".

## REST API as an OAuth Resource Server (`OAUTH_RS_ENABLED`)

Independent of Admin UI SSO above: that is the browser login, this lets a
program call the REST API with an isap-auth access token instead of a local
password or an API key. Set in `.env` (all four go to `apihub-api`):

    OAUTH_RS_ENABLED=true
    OAUTH_ISSUER=https://<GATEWAY_SERVER_NAME>/auth
    OAUTH_INTROSPECTION_URL=http://isap-auth:9997/auth/api/v1/introspect
    OAUTH_INTROSPECTION_SECRET=<isap-auth's own OAUTH_INTROSPECTION_SECRET>

The secret is **copied from the isap-auth deployment**, not generated here —
the same value the MCP add-on uses as `MCP_INTROSPECTION_SECRET`.

Discovery needs one manual gateway step, for the same reason as the MCP
snippet: RFC 9728 clients build the metadata URL at the domain root
(`/.well-known/oauth-protected-resource/apihub`), which no app-scoped
`location` block can match. Hand
`gateway-conf/oauth-wellknown-apihub.snippet.conf` to the platform operator to
paste into the shared root well-known conf. Without it the API still accepts
OAuth tokens; only auto-discovery breaks.

### Verification (requires live environment)

1. `curl https://<GATEWAY_SERVER_NAME>/apihub/.well-known/oauth-protected-resource`
   returns the resource + authorization server (404 means the flag is off).
2. A call with no credential returns 401 with a `WWW-Authenticate` header
   naming that same URL.
3. With an isap-auth access token: `curl -H "Authorization: Bearer <token>"
   https://<GATEWAY_SERVER_NAME>/apihub/api/v1/auth/me` returns the SSO user.
4. That user's audit rows show `principal_type=sso_user` with their own
   username — not a shared service account.
5. With `SSO_AUTOCREATE=false`, a valid token for an unprovisioned user gets
   **403 "User not provisioned"** (not 401 — the token is fine, the account
   is missing).
6. Revoking centrally (isap-auth logout, or disabling the user) stops working
   tokens within `OAUTH_INTROSPECTION_CACHE_SECONDS` (default 60s), not at the
   next JWT expiry.

Unattended callers (Celery, cron, `scripts/extract_query.py`) keep using API
keys: isap-auth has no `client_credentials` grant.

## MCP server (`apihub-mcp`, OAuth 2.1 Resource Server)

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

### New `.env.mcp` values (see `.env.mcp.example` for full comments)

These belong to the MCP add-on's `.env.mcp`, except `MCP_BRIDGE_SECRET` and
`MCP_BRIDGE_AUTOCREATE`, which the api reads from `.env` and which must match
the `.env.mcp` value.

| Var | Notes |
|---|---|
| `MCP_PUBLIC_URL` | `https://<GATEWAY_SERVER_NAME>/apihub/mcp` — the RS resource identifier clients see. |
| `MCP_AUTH_ISSUER` | `https://<GATEWAY_SERVER_NAME>/auth` — isap-auth's issuer. |
| `MCP_INTROSPECTION_SECRET` | **Shared with isap-auth.** Must be the exact same value isap-auth's own `OAUTH_INTROSPECTION_SECRET` uses for its `/auth/api/v1/introspect` endpoint — copy it from the isap-auth deployment, do not generate a new one here. |
| `MCP_BRIDGE_SECRET` | New per-deployment secret (`openssl rand -base64 32`). Required in both `apihub-api`'s env (activates the bridge endpoint) and `apihub-mcp`'s env (used to call it) — the compose file wires the same `.env` value into both services. |
| `MCP_BRIDGE_AUTOCREATE` | Optional, default `false`. `true` auto-creates a role-less local user on first SSO login instead of rejecting unknown SSO users with 403. |

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

### End-to-end verification checklist (post-deployment verification — requires live environment)

Cannot be executed from this repo/session; run these against the real
gateway + isap-auth once deployed, in order. Based on ITSM-PM's
`mcp-technical-report.html` §10.6 step 10 checklist.

```bash
# 1. AS metadata (platform-level, already live if isap-auth is deployed)
curl -s https://<GATEWAY_SERVER_NAME>/auth/.well-known/oauth-authorization-server
# expect: 200, JSON with issuer/authorization_endpoint/token_endpoint

# 2. RS metadata (this app's protected-resource document)
curl -s https://<GATEWAY_SERVER_NAME>/.well-known/oauth-protected-resource/apihub/mcp
# expect: 200, {"resource": "https://.../apihub/mcp", "authorization_servers": ["https://.../auth"]}

# 3. Unauthenticated MCP call -> 401 challenge
curl -si https://<GATEWAY_SERVER_NAME>/apihub/mcp/mcp/ -X POST
# expect: 401 with WWW-Authenticate: Bearer resource_metadata="https://.../apihub/mcp/.well-known/oauth-protected-resource"
```

Then from a client:

4. `claude mcp add apihub --transport http https://<GATEWAY_SERVER_NAME>/apihub/mcp/mcp/`
   (note the double `/mcp/mcp/` — gateway prefix `/apihub/mcp/` + the
   Streamable HTTP mount `/mcp/`, trailing slash required) — completes a
   browser SSO flow via isap-auth.
5. A tool call (e.g. `list_databases`) succeeds; the API Hub audit log
   records the SSO user's own username (not a shared service account).
6. A query tool against a definition the SSO user lacks permission for
   returns a 403, surfaced as a tool error (RBAC enforced end-to-end).
7. Log out of SSO; the next tool call gets 401 — isap-auth introspection now
   returns `active: false` and the identity bridge refuses to mint a new JWT.

## Update

```bash
git pull
docker compose up -d --build      # rebuilds image, recreates containers
```

## Decommission

```bash
docker compose -f docker-compose.mcp.yml down   # if the MCP add-on was started
docker compose down
rm -f /opt/isap/gateway-apps/apihub.conf /opt/isap/gateway-apps/apihub-mcp.conf
# The root well-known block was added manually — delete the apihub/mcp
# location block from the shared root conf by hand.
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
