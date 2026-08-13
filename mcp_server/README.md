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
| Editing a **role** so a caller may see more or fewer definitions | Next TTL lapse or reconnect | Next reconnect |

### How the fingerprint works

`GET /api/v1/mcp/catalog-version` returns a hash of everything that shapes the
catalog — the alias list plus each definition's name, group, description,
result_description, permission and parameter declarations. **Not the SQL**: an
SQL edit changes results, not tools, and is already live, so hashing it would
force pointless rebuilds.

That one small request replaces the refetch-everything check, which is what
makes a short TTL affordable: on expiry `ToolCatalog` asks for the fingerprint
and only reloads every alias when it moved. A Hub too old to answer degrades to
the previous always-refetch behaviour rather than serving a stale list.

### Why stdio can push and HTTP cannot

stdio holds one long-lived `ServerSession`, so a background poller
(`watch_catalog`) can compare fingerprints and call `send_tool_list_changed()`.
The session is latched by `SessionTracker` on the first handler call, because
`Server.run()` builds it internally and never hands it back.

The HTTP transport runs `stateless=True` (see the module docstring in
`http_app.py`): each request gets its own short-lived session, so there is
nothing to push to later, and the `tools_changed` capability is correctly never
declared there. Most clients call `tools/list` once per connection, so
**lowering the TTL does nothing for an HTTP client** — it will not ask again.
For HTTP, reconnect (`/mcp` in Claude Code), or call `refresh_query_tools`,
which rebuilds the server-side catalog and reports what it dropped — though the
client still shows its cached list until it issues another `tools/list`.

Making HTTP live too needs stateful sessions with per-session credential
binding, which the current passthrough design deliberately rules out.

Set `MCP_WATCH_INTERVAL_SECONDS=0` to disable the poller; the `tools_changed`
capability is then not advertised either, so a client is never told to expect
notifications that will not arrive.

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
`MCP_CATALOG_TTL_SECONDS` and `MCP_WATCH_INTERVAL_SECONDS` are covered under
Freshness above.
