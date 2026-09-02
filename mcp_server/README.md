# Oracle API Hub MCP Server

Exposes registered SQL definitions as MCP tools. Thin HTTP proxy over the
REST API — RBAC, audit, and rate limiting apply per caller, unchanged.

## Tools

- `query_<db>_<name>` — one per SQL definition (dynamic; `fetch_mode` of
  `page` / `count` / `one`, page_size ≤ MCP_PAGE_SIZE_MAX). `all` and `batch`
  are withheld: both return every matching row, and a six-figure result set does
  not belong in a model's context — that is what `scripts/extract_query.py` is
  for. The allowlist is enforced in `dispatch_tool`, not only in the tool's enum.
- `list_databases` / `describe_table` — schema discovery
- `refresh_query_tools` — rebuild the tool list after definition changes; reports
  definitions dropped for a name collision (`shadowed`) or with a truncated name

The catalog is fetched with `?permitted_only=true`, so `tools/list` shows only
the definitions the caller's own credential could execute — the list goes into
an LLM's context, and an unfiltered one would leak the names, descriptions and
parameters of everything else on the alias.

## Tool descriptions

`catalog.py::build_description` composes what the model reads when choosing
between tools:

    [dw · HR] Headcount by department as of a frozen date.
    Returns: EMP_NO, NAME, DEPT, HIRE_DATE

- the header carries the alias and the definition's `group_name` — the same
  taxonomy the Admin UI groups by, so a model picking among forty similar
  tools gets the operator's categories instead of guessing from names;
- the body is `description`, falling back to `Run registered query '<name>'`;
- `Returns:` is `result_description`, omitted when empty. Nothing validates it
  against the real result set — it is documentation, and worth writing: without
  it the only way to learn a query's shape is to run it.

Both fields are optional and editable in the Admin UI's definition form.

Parameter schemas come from `param_schema`, which **appends** a per-type hint to
the operator's own description rather than replacing it — a `date` param reads
`<your description> — Format: YYYY-MM-DD (e.g. 2026-08-01).` The hint lives in
the description because models follow stated formats far more reliably than the
JSON Schema `format` keyword, and the binds reach the database as strings, so a
wrong shape is a runtime error the dialect layer cannot rescue.

## Freshness — when a definition change reaches the LLM

Three different answers, and conflating them wastes a lot of debugging time:

| Change | stdio | Streamable HTTP |
|---|---|---|
| Editing a definition's **SQL** | **Immediately** — tool calls are proxied live to `POST /{db}/queries/execute`, which reads the Hub's registry. No catalog is involved. | same |
| **Adding, renaming or deleting** a definition, or changing its **description / result_description / group_name / params** | **Within `MCP_WATCH_INTERVAL_SECONDS`** (default 30) — pushed as `notifications/tools/list_changed`. | **Not until the client reconnects.** |
| Editing a **role** so a caller may see more or fewer definitions | Next `tools/list` | Next reconnect |

### How the fingerprint works

`GET /api/v1/mcp/catalog-version` returns a hash of everything that shapes the
catalog — the alias list plus each definition's name, group, description,
result_description, permission and parameter declarations. **Not the SQL**: an
SQL edit changes results, not tools, and is already live, so hashing it would
force pointless rebuilds.

The stdio watcher polls it instead of refetching every definition to notice a
change, which is what makes a 30-second interval affordable. A Hub too old to
answer simply never matches, so the watcher reloads each tick rather than going
silent.

### The tool list is cached per credential, gated on the fingerprint

`ToolCatalog` keeps one entry per caller credential (a SHA-256 of the
passthrough headers on HTTP; a constant on stdio, which has one caller) and
only ever serves an entry to the key that populated it. The list is
permission-filtered per caller (`permitted_only`) and one process serves every
HTTP caller, so a **process-wide** copy would serve whichever caller populated
it to everyone else — handing a restricted user the names, descriptions and
parameters of tools they cannot run. Keying on the credential is what makes
holding it safe.

Every `tools/list` still costs one `catalog-version` request; a hit needs the
fingerprint to match *and* the entry to be younger than
`MCP_CATALOG_TTL_SECONDS` (default 60). The fingerprint does not cover roles,
so the TTL is the role-change lag — the same 60 s OAuth introspection already
imposes. `0` disables the cache. A Hub without the version endpoint is never
cached.

Why it matters: a full load is `catalog-version` + `/databases` (skipped when
`MCP_DB_ALIASES` is set) + one `definitions` per alias, and **every one of those
counts against the caller's REST rate limit** — the same bucket their queries
draw from. Open WebUI opens a fresh session and re-lists tools on every chat
turn, so at five aliases the catalog, not the queries, was what exhausted the
default 60/min. `tools/call` consults the name → target map and does not
rebuild the list either; it used to, which made one tool call cost eight REST
requests.

The name → target map *is* shared and accumulates across callers. It is derived
from naming alone, discloses nothing, and dispatching a target without
permission still 403s at the REST layer; it has to accumulate, or a restricted
caller's load would erase entries a privileged caller needs.

### Why stdio can push and HTTP cannot

stdio holds one long-lived `ServerSession`, so a background poller
(`watch_catalog`) can compare fingerprints and call `send_tool_list_changed()`.
The session is latched by `SessionTracker` on the first handler call, because
`Server.run()` builds it internally and never hands it back.

The HTTP transport runs `stateless=True` (see the module docstring in
`http_app.py`): each request gets its own short-lived session, so there is
nothing to push to later, and the `tools_changed` capability is correctly never
declared there. Most clients call `tools/list` once per connection, so an
HTTP client sees a new definition only when it reconnects (`/mcp` in Claude
Code) — the server always has the current list ready; the client just does not
ask. `refresh_query_tools` reports what a rebuild dropped, but likewise cannot
make the client re-list.

Making HTTP live too needs stateful sessions with per-session credential
binding, which the current passthrough design deliberately rules out.

Set `MCP_WATCH_INTERVAL_SECONDS=0` to disable the poller; the `tools_changed`
capability is then not advertised either, so a client is never told to expect
notifications that will not arrive.

The paging arguments are normally `page` / `page_size`. A definition that declares
a bind parameter of its own by either name keeps it — the paging knob steps aside
to `_page` / `_page_size` for that tool only, so check the tool's schema rather
than assuming the plain names.

`describe_table`'s `table` accepts `NAME` or `SCHEMA.NAME` over `[A-Za-z0-9_$#]`;
anything else is rejected before a request goes out, so a model cannot steer the
proxy at another REST endpoint.

Every tool declares an `outputSchema` and returns `structuredContent` as well as
the JSON text block, carries `annotations` (all read-only except
`refresh_query_tools`, which rebuilds this process's catalog), and has a display
`title`. Calling a name that is not registered answers with a JSON-RPC `-32602`
protocol error rather than an `isError` result.

Tool names are capped at 128 characters (the current spec's limit; it was 64,
inherited from a superseded one, which is what used to truncate long names into
collisions). Collisions are still resolved first-wins — see the Admin UI's
**MCP Tools** page for which definitions lose.

## Transports & auth

| Transport | Run | Auth |
|---|---|---|
| stdio | `python -m mcp_server` | `MCP_API_HUB_API_KEY` env (service-account key) |
| Streamable HTTP | `uvicorn mcp_server.http_app:app --port 8810` | Caller's `X-API-Key` / `Authorization` header, passed through to the REST API |

## Claude Code registration

    claude mcp add apihub --transport stdio \
      -e MCP_API_HUB_URL=http://localhost:8000 \
      -e MCP_API_HUB_API_KEY=<key> \
      -- python -m mcp_server

    # or HTTP (note: /mcp/ with trailing slash — required)
    claude mcp add apihub-http --transport http http://localhost:8810/mcp/ \
      --header "X-API-Key: <key>"

## Health

`GET /health` (HTTP transport) reports this process **and** whether the REST API
answers, returning 503 when it does not — a pure proxy that is alive but cut off
is not healthy, and used to report `{"status": "ok"}` regardless.

Do **not** wire it to a compose `healthcheck:` with restart semantics: restarting
the proxy cannot fix an unreachable API, it only adds a restart loop for the
length of the outage. It is for monitoring.

## Settings

All via `MCP_*` env vars or `.env.mcp` — see `mcp_server/config.py`.
Phase 2 (OAuth 2.1 Resource Server, isap-auth) is toggled by `MCP_OAUTH_ENABLED`.
`MCP_WATCH_INTERVAL_SECONDS` is covered under Freshness above.

`MCP_REQUEST_TIMEOUT_SECONDS` (default 120) caps one REST call. Set it above
the slowest definition being exposed: when it expires the client reports a
failure and the model usually re-issues the same tool call, while the Hub is
still executing the abandoned one — so a timeout below the real query time
turns one slow query into a growing pile of duplicates on the API.

isap-auth serves every app on the gateway, so "the token introspects as active"
does not mean it was issued for this server. An incoming token must name us in an
`aud` / `resource` / `resources` / `audience` claim — compared against
`MCP_OAUTH_AUDIENCE`, or `MCP_PUBLIC_URL` when that is unset — or it is rejected
with 401 before any credential is forwarded downstream. The check **fails
closed**: a token carrying no audience claim at all is rejected, so confirm what
isap-auth emits before turning OAuth on (see `deploy/apihub/.env.mcp.example`).

The gate distinguishes its failures: **401** (no token, inactive, or not issued
for us) carries the `WWW-Authenticate` challenge; **403** means the token was
fine but the Hub would not mint a local identity for that person (bridge mode
only); **503** means isap-auth or the identity bridge could not be reached, and
deliberately carries no challenge — a 401 there would send the client round the
whole authorization flow for an outage. Only the definitive verdicts are cached;
an outage is retried.

`MCP_OAUTH_SCOPES` (space- or comma-separated) is advertised as
`scopes_supported` in the resource metadata and as `scope=` in the challenge, so
clients request those instead of every scope the authorization server offers.
When unset, a built-in default (`openid profile email`) is advertised instead:
clients derive their authorize request from this list, and an empty one would
stop them appending `offline_access` — the only scope that earns a refresh token
from isap-auth.

The HTTP transport validates `Origin` (403 on mismatch) and `Host` (421). Both
allow-lists are derived from `MCP_PUBLIC_URL`, overridable with
`MCP_ALLOWED_ORIGINS` / `MCP_ALLOWED_HOSTS`. With no public URL configured — local
development — the checks stay off. A request with no `Origin` header is always
allowed; that is every non-browser MCP client, and not the threat being addressed.
