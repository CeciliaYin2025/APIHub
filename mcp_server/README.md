# Oracle API Hub MCP Server

Exposes registered SQL definitions as MCP tools. Thin HTTP proxy over the
REST API — RBAC, audit, and rate limiting apply per caller, unchanged.

## Tools

- `query_<db>_<name>` — one per SQL definition (dynamic; paged, page_size ≤ MCP_PAGE_SIZE_MAX)
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

Two different answers, and conflating them wastes a lot of debugging time:

| Change | Visible to a connected client |
|---|---|
| Editing a definition's **SQL** | **Immediately.** Tool calls are proxied live to `POST /{db}/queries/execute`, which reads the Hub's registry — no MCP-side cache is involved. |
| **Adding, renaming or deleting** a definition, or changing its **description / result_description / group_name / params** | **Not until the client reconnects.** |

Three things stack up to produce that second row:

1. `ToolCatalog` caches the tool list for `MCP_CATALOG_TTL_SECONDS` (default 300).
2. An expired TTL only means the *next* `tools/list` refetches — nothing refreshes on its own.
3. The server never sends `notifications/tools/list_changed`, and does not
   declare the capability: `create_initialization_options()` is called with the
   SDK's default `NotificationOptions` (`tools_changed=False`). The HTTP
   transport also runs `stateless=True` (see the module docstring in
   `http_app.py`), so there is no persistent session to push over.

Most clients — Claude Code and Claude Desktop included — call `tools/list` once
when the connection is established. **Lowering the TTL therefore does not help
an HTTP client**: it will not ask again regardless.

To pick up a new definition:

- reconnect the MCP client (`/mcp` in Claude Code), or
- call `refresh_query_tools`, which rebuilds the server-side catalog — the
  client still shows its cached list until it issues another `tools/list`.

Making this live for stdio needs `NotificationOptions(tools_changed=True)` plus
a poller calling `session.send_tool_list_changed()`. Making it live for HTTP
additionally needs stateful sessions with per-session credential binding, which
the current passthrough design deliberately rules out.

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

## Settings

All via `MCP_*` env vars or `.env.mcp` — see `mcp_server/config.py`.
Phase 2 (OAuth 2.1 Resource Server, isap-auth) is toggled by `MCP_OAUTH_ENABLED`.
